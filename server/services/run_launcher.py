"""Background execution of molecular optimization runs."""

from __future__ import annotations

import os
import platform
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.orm.exc import DetachedInstanceError

from server.audit import AuditEventType, AuditSeverity, audit_logger
from server.database import get_db_context
from server.experiment_logger import ExperimentError, experiment_logger
from server.models.user import User
from server.services.cache_service import cache_service
from server.services.run_events import run_event_hub
from server.services.run_service import run_service
from server.storage import results_json_path, summary_txt_path, run_dir
from run_workflow import WorkflowRunner


def start_run(run_id: str) -> None:
    """Launch workflow execution on a background thread."""

    thread = threading.Thread(
        target=_run_workflow_background,
        name=f"workflow-{run_id}",
        args=(run_id,),
        daemon=True,
    )
    thread.start()


def get_environment_info() -> Dict[str, str]:
    """Collect environment information for reproducibility."""

    def get_git_commit() -> Optional[str]:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent.parent.parent,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            return None
        return None

    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "hostname": platform.node(),
        "git_commit": get_git_commit() or "unknown",
    }


def _run_workflow_background(run_id: str) -> None:
    """Execute a workflow run and manage lifecycle side-effects."""

    with get_db_context() as db:
        run_model = run_service.get_run(db, run_id)
        if not run_model:
            return

        metadata = run_model.extra_metadata or {}
        experiment_id = metadata.get("experiment_id")
        max_iterations = metadata.get("max_iterations")
        batch_size = metadata.get("batch_size")
        paths: Dict[str, str] = metadata.get("paths", {}) if isinstance(metadata, dict) else {}

        user_id = str(run_model.user_id)
        username = None
        if run_model.user is not None:
            try:
                username = run_model.user.username
            except DetachedInstanceError:
                username = None
        if not username:
            user_obj = db.query(User).filter(User.id == run_model.user_id).first()
            username = user_obj.username if user_obj else "unknown"

        prompt = getattr(run_model, "prompt", "")

    experiment = experiment_logger.get_experiment(experiment_id) if experiment_id else None
    if experiment:
        experiment.environment = get_environment_info()
        experiment_logger.update_experiment(experiment)

    environment = os.getenv("ENVIRONMENT", "development").lower()

    # Mark experiment start
    if experiment:
        experiment.mark_started()
        experiment_logger.update_experiment(experiment)

    audit_logger.log(
        event_type=AuditEventType.EXPERIMENT_STARTED,
        message=f"Started experiment {experiment_id} for run {run_id}",
        user_id=user_id,
        username=username,
        experiment_id=experiment_id,
        run_id=run_id,
        details={"prompt": prompt},
    )

    if environment == "testing":
        with get_db_context() as db:
            run_service.update_run_status(db, run_id, "completed", "testing-shortcut")
            run_model = run_service.get_run(db, run_id)

        if run_model:
            cached_info = run_service.run_to_info(run_model)
            cache_service.cache_run(run_id, cached_info.model_dump())

        cache_service.invalidate_user_runs_list(user_id)

        run_event_hub.append_log(run_id, {
            "timestamp": datetime.now().isoformat(),
            "message": "Workflow skipped in testing environment",
            "level": "INFO",
        })

        if experiment:
            experiment.mark_completed()
            experiment_logger.update_experiment(experiment)

        audit_logger.log(
            event_type=AuditEventType.EXPERIMENT_COMPLETED,
            message=f"Completed experiment {experiment_id} for run {run_id} (testing shortcut)",
            user_id=user_id,
            username=username,
            experiment_id=experiment_id,
            run_id=run_id,
            details={"status": "success", "mode": "testing"},
        )

        _notify_capacity_available(run_id)
        return

    runner = WorkflowRunner(checkpoint_dir=str(run_dir(run_id) / "checkpoints"))

    def emit(event: Dict[str, Any]) -> None:
        run_event_hub.append_log(run_id, event)

        if experiment:
            experiment.add_log(
                message=event.get("message", str(event)),
                level=event.get("level", "INFO"),
                node=event.get("node"),
                iteration=event.get("iteration"),
                data=event,
            )

        starting = None
        data = event.get("data")
        if isinstance(data, dict) and data.get("starting_molecules"):
            starting = data.get("starting_molecules")
        elif event.get("starting_molecules"):
            starting = event.get("starting_molecules")

        if starting:
            if isinstance(starting, (list, tuple, set)):
                normalized = [str(m) for m in starting]
            else:
                normalized = [str(starting)]

            with get_db_context() as db:
                run_service.update_run_molecules(db, run_id, normalized)

            cache_service.invalidate_run(run_id)
            cache_service.invalidate_user_runs_list(user_id)

    try:
        state = runner.run(
            user_prompt=prompt,
            checkpoint_path=None,
            save_checkpoints=True,
            event_callback=emit,
            run_paths=paths or None,
        )

        starting_molecules = list(state.starting_molecules or [])
        if experiment and starting_molecules:
            experiment.metadata["starting_molecules"] = starting_molecules

        if experiment:
            experiment.parsed_arguments = state.parsed_arguments
            experiment.targets = [t.model_dump() for t in state.targets]
            experiment.best_molecules = state.best_molecules
            experiment.metrics.iterations_completed = state.current_iteration
            experiment.metrics.molecules_evaluated = len(state.experimental_results)
            experiment.metrics.bo_iterations = len(state.bo_rounds)

        results_path = results_json_path(run_id)
        results_path.parent.mkdir(parents=True, exist_ok=True)
        runner.export_results(state, str(results_path))

        if state.summary:
            summary_path = summary_txt_path(run_id)
            summary_path.write_text(state.summary)
            if experiment:
                experiment.summary = state.summary

        with get_db_context() as db:
            run_service.update_run_status(db, run_id, str(state.status), state.exit_reason)
            if starting_molecules:
                run_service.update_run_molecules(db, run_id, starting_molecules)

        cache_service.invalidate_run(run_id)
        cache_service.invalidate_user_runs_list(user_id)

        run_event_hub.append_log(run_id, {
            "timestamp": datetime.now().isoformat(),
            "message": f"Workflow completed with status {state.status}",
            "level": "INFO",
        })

        if experiment:
            experiment.mark_completed()
            experiment_logger.update_experiment(experiment)

        audit_logger.log(
            event_type=AuditEventType.EXPERIMENT_COMPLETED,
            message=f"Completed experiment {experiment_id} for run {run_id}",
            user_id=user_id,
            username=username,
            experiment_id=experiment_id,
            run_id=run_id,
            details={
                "status": str(state.status),
                "molecules_evaluated": len(state.experimental_results),
            },
        )

    except Exception as exc:
        error = ExperimentError(
            error_type=type(exc).__name__,
            message=str(exc),
            stack_trace="",
        )
        if experiment:
            experiment.mark_failed(error)
            experiment_logger.update_experiment(experiment)

        with get_db_context() as db:
            run_service.update_run_status(db, run_id, "failed", str(exc))

        cache_service.invalidate_run(run_id)
        cache_service.invalidate_user_runs_list(user_id)

        run_event_hub.append_log(run_id, {
            "timestamp": datetime.now().isoformat(),
            "message": f"Workflow failed: {exc}",
            "level": "ERROR",
        })

        audit_logger.log(
            event_type=AuditEventType.EXPERIMENT_FAILED,
            message=f"Experiment {experiment_id} failed for run {run_id}",
            user_id=user_id,
            username=username,
            experiment_id=experiment_id,
            run_id=run_id,
            severity=AuditSeverity.ERROR,
            details={"error": str(exc)},
        )

    if experiment:
        experiment_logger.update_experiment(experiment)

    _notify_capacity_available(run_id)


def _notify_capacity_available(run_id: str) -> None:
    """Signal the scheduler that capacity may be available."""
    try:
        from server.services.run_scheduler import run_scheduler

        run_scheduler.on_run_finished(run_id)
    except Exception:
        # Scheduler notifications are best-effort
        return
