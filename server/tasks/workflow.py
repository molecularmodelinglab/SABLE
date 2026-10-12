import logging
import os
from datetime import datetime
from typing import Any, Dict

from celery import shared_task
from sqlalchemy.orm.exc import DetachedInstanceError

from server.audit import AuditEventType, AuditSeverity, audit_logger
from server.database import get_db_context
from server.experiment_logger import ExperimentError, experiment_logger
from server.models.user import User
from server.services.run_events import run_event_hub
from server.services.run_execution import LeaseLost, RunLease, claim_run
from server.services.run_service import run_service
from server.storage import results_json_path, summary_txt_path, run_dir, sync_run
from run_workflow import WorkflowRunner

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="server.tasks.workflow.run_workflow",
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_workflow_task(self, run_id: str, dispatch_token: str | None = None) -> None:
    """Execute only the current reservation, with a renewable ownership lease."""
    if not dispatch_token:
        logger.warning("Ignoring workflow delivery without a reservation for run %s", run_id)
        return
    lease = claim_run(run_id, dispatch_token)
    if lease is None:
        return
    try:
        with lease:
            try:
                status, exit_reason, molecules = _execute_workflow(run_id, lease)
                lease.check()
                if not lease.finish(status, exit_reason, molecules):
                    logger.warning("Workflow ownership lost before finalization for run %s", run_id)
            except LeaseLost:
                logger.warning("Stopping workflow after ownership loss for run %s", run_id)
            except Exception as exc:
                if lease.finish("failed", str(exc)):
                    _record_setup_failure(run_id, exc)
                raise
    finally:
        _notify_capacity_available(run_id)


def _record_setup_failure(run_id: str, exc: Exception) -> None:
    """Report failures outside the runner's own error handler after releasing capacity."""
    try:
        with get_db_context() as db:
            run_model = run_service.get_run(db, run_id)
            metadata = (run_model.extra_metadata or {}) if run_model else {}
            experiment_id = metadata.get("experiment_id")
        experiment = experiment_logger.get_experiment(experiment_id) if experiment_id else None
        if experiment:
            experiment.mark_failed(ExperimentError(
                error_type=type(exc).__name__, message=str(exc), stack_trace=""
            ))
            experiment_logger.update_experiment(experiment)
        run_event_hub.append_log(run_id, {
            "timestamp": datetime.now().isoformat(),
            "message": f"Workflow failed: {exc}",
            "level": "ERROR",
        })
    except Exception:
        logger.exception("Could not record workflow failure for run %s", run_id)


def _execute_workflow(run_id: str, lease: RunLease) -> tuple:
    task_id = run_workflow_task.request.id
    lease.check()

    with get_db_context() as db:
        run_model = run_service.get_run(db, run_id)
        if not run_model:
            raise LeaseLost("Run was removed before execution")

        metadata = run_model.extra_metadata or {}
        experiment_id = metadata.get("experiment_id")
        paths: Dict[str, str] = metadata.get("paths", {}) if isinstance(metadata, dict) else {}
        characterization_config = metadata.get("characterization", {})

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
        # TODO: Add worker environment info
        # experiment.environment = get_environment_info() 
        experiment_logger.update_experiment(experiment)

    environment = os.getenv("ENVIRONMENT", "development").lower()

    # Mark experiment start
    if experiment:
        experiment.mark_started()
        experiment_logger.update_experiment(experiment)

    audit_logger.log(
        event_type=AuditEventType.EXPERIMENT_STARTED,
        message=f"Started experiment {experiment_id} for run {run_id} (Task {task_id})",
        user_id=user_id,
        username=username,
        experiment_id=experiment_id,
        run_id=run_id,
        details={"prompt": prompt, "task_id": task_id},
    )

    if environment == "testing":
        lease.check()
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

        lease.check()
        sync_run(run_id)
        return "completed", "testing-shortcut", None

    lease.check()
    runner = WorkflowRunner(checkpoint_dir=str(run_dir(run_id) / "checkpoints"))

    # Check for existing checkpoints to resume
    checkpoint_path = None
    try:
        checkpoints = list(runner.checkpoint_dir.glob("*.pkl"))
        if checkpoints:
            latest_checkpoint = max(checkpoints, key=lambda p: p.stat().st_mtime)
            checkpoint_path = str(latest_checkpoint)
            
            audit_logger.log(
                event_type=AuditEventType.SYSTEM_EVENT,
                message=f"Resuming run {run_id} from checkpoint {latest_checkpoint.name}",
                severity=AuditSeverity.INFO,
                details={"checkpoint": str(latest_checkpoint)}
            )
    except Exception as e:
        print(f"Error finding checkpoints: {e}")

    def emit(event: Dict[str, Any]) -> None:
        lease.check()
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

            if not lease.update_molecules(normalized):
                raise LeaseLost("Workflow ownership lost while updating molecules")

    starting_molecules = None
    try:
        lease.check()
        state = runner.run(
            user_prompt=prompt,
            checkpoint_path=checkpoint_path,
            save_checkpoints=True,
            event_callback=emit,
            run_paths=paths or None,
            characterization_config=characterization_config or None,
            extensions={"execution": {"run_id": run_id, "user_id": user_id}},
        )

        lease.check()
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
        lease.check()
        runner.export_results(state, str(results_path))
        lease.check()

        if state.summary:
            summary_path = summary_txt_path(run_id)
            summary_path.write_text(state.summary)
            if experiment:
                experiment.summary = state.summary

        status = str(getattr(state.status, "value", state.status))
        exit_reason = state.exit_reason
        lease.check()
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

    except LeaseLost:
        raise
    except Exception as exc:
        lease.check()
        status, exit_reason = "failed", str(exc)
        error = ExperimentError(
            error_type=type(exc).__name__,
            message=str(exc),
            stack_trace="",
        )
        if experiment:
            experiment.mark_failed(error)
            experiment_logger.update_experiment(experiment)

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

    lease.check()
    if experiment:
        experiment_logger.update_experiment(experiment)

    sync_run(run_id)
    lease.check()
    return status, exit_reason, starting_molecules


def _notify_capacity_available(run_id: str) -> None:
    """Signal the scheduler that capacity may be available."""
    try:
        from server.services.run_scheduler import run_scheduler

        run_scheduler.on_run_finished(run_id)
    except Exception:
        # scheduler notifications are best-effort
        return
