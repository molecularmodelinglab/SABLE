"""Run management API endpoints."""

import json
import shutil
import queue
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Callable, Deque
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session as DBSession

from server.database import get_db, get_db_context
from server.models.user import User
from server.auth.dependencies import get_current_user, get_current_active_user
from server.schemas import RunCreateRequest, RunInfo, RunList
from server.storage import ensure_run_dirs, results_json_path, summary_txt_path, run_dir
from server.services.run_service import run_service
from server.services.cache_service import cache_service
from server.experiment_logger import experiment_logger, ExperimentError
from server.audit import audit_logger, AuditEventType, AuditSeverity
from run_workflow import WorkflowRunner

router = APIRouter(prefix="/runs", tags=["runs"])

# Subscribers for Server-Sent Events
_SUBSCRIBERS: Dict[str, list[Callable[[Dict[str, Any]], None]]] = {}


def get_environment_info() -> Dict[str, str]:
    """Collect environment information for reproducibility."""
    import platform
    import subprocess

    def get_git_commit() -> str | None:
        """Get current git commit hash."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent.parent.parent
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "hostname": platform.node(),
        "git_commit": get_git_commit() or "unknown",
    }


def check_run_authorization(run_id: str, user: User, db: DBSession) -> RunInfo:
    """
    Check if user has access to a run.

    Uses Redis cache with database fallback for performance.

    Args:
        run_id: Run identifier
        user: Current authenticated user
        db: Database session

    Returns:
        RunInfo if authorized

    Raises:
        HTTPException: If not authorized
    """
    # Try cache first for performance
    cached_run = cache_service.get_cached_run(run_id)
    if cached_run:
        # Verify user ownership from cache
        if cached_run.get("user_id") == str(user.id):
            # Convert dict back to RunInfo
            info = RunInfo(**cached_run)
            info.username = user.username
            return info
        else:
            # Unauthorized access attempt
            audit_logger.log(
                event_type=AuditEventType.UNAUTHORIZED_ACCESS,
                message=f"User {user.username} attempted to access run {run_id}",
                user_id=str(user.id),
                username=user.username,
                run_id=run_id,
                severity=AuditSeverity.WARNING,
                success=False,
                details={"owner_user_id": cached_run.get("user_id")}
            )
            raise HTTPException(403, "Access denied: You can only access your own runs")

    # Cache miss - fetch from database
    run_model = run_service.get_run(db, run_id, str(user.id))
    if not run_model:
        raise HTTPException(404, "Run not found")

    # Convert to RunInfo
    paths = run_model.metadata.get("paths", {}) if run_model.metadata else {}
    info = run_service.run_to_info(
        run_model,
        summary_available=Path(summary_txt_path(run_model.id)).exists() if paths else False,
        results_available=Path(results_json_path(run_model.id)).exists() if paths else False,
        paths=paths
    )
    info.username = user.username

    # Cache for future requests
    cache_service.cache_run(run_id, info.model_dump())

    return info


def _append_log(run_id: str, event: Dict[str, Any]):
    """Append event to run logs."""
    log_path = run_dir(run_id) / "logs" / "logs.ndjson"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(json.dumps(event) + "\n")

    # Fan-out to in-memory subscribers
    for cb in _SUBSCRIBERS.get(run_id, []):
        try:
            cb(event)
        except Exception:
            pass


def _run_workflow_background(
    run_id: str,
    prompt: str,
    max_iterations: int | None,
    batch_size: int | None,
    experiment_id: str,
    user_id: str,
    username: str
):
    """Run workflow in background."""
    experiment = experiment_logger.get_experiment(experiment_id)
    if not experiment:
        return

    runner = WorkflowRunner(checkpoint_dir=str(run_dir(run_id) / "checkpoints"))

    # Get database context for updates
    db_context = get_db_context()

    # Mark experiment as started
    experiment.mark_started()
    experiment.environment = get_environment_info()
    experiment_logger.update_experiment(experiment)

    # Log experiment start
    audit_logger.log(
        event_type=AuditEventType.EXPERIMENT_STARTED,
        message=f"Started experiment {experiment_id} for run {run_id}",
        user_id=user_id,
        username=username,
        experiment_id=experiment_id,
        run_id=run_id,
        details={"prompt": prompt}
    )

    def emit(event: Dict[str, Any]):
        _append_log(run_id, event)

        # Also log to experiment
        if experiment:
            experiment.add_log(
                message=event.get("message", str(event)),
                level=event.get("level", "INFO"),
                node=event.get("node"),
                iteration=event.get("iteration"),
                data=event
            )

        # Capture starting molecules
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

            # Update database
            with db_context as db:
                run_service.update_run_molecules(db, run_id, normalized)

            # Invalidate cache since run data changed
            cache_service.invalidate_run(run_id)
            cache_service.invalidate_user_runs_list(user_id)

    try:
        # Run the workflow
        state = runner.run(
            user_prompt=prompt,
            checkpoint_path=None,
            save_checkpoints=True,
            event_callback=emit
        )

        starting_molecules = list(state.starting_molecules or [])
        if starting_molecules:
            experiment.metadata["starting_molecules"] = starting_molecules

        # Update experiment with results
        experiment.parsed_arguments = state.parsed_arguments
        experiment.targets = [t.model_dump() for t in state.targets]
        experiment.best_molecules = state.best_molecules
        experiment.metrics.iterations_completed = state.current_iteration
        experiment.metrics.molecules_evaluated = len(state.experimental_results)
        experiment.metrics.bo_iterations = len(state.bo_rounds)

        # Export results
        results_path = results_json_path(run_id)
        results_path.parent.mkdir(parents=True, exist_ok=True)
        runner.export_results(state, str(results_path))

        # Write summary
        if state.summary:
            s_path = summary_txt_path(run_id)
            s_path.write_text(state.summary)
            experiment.summary = state.summary

        # Update database
        with db_context as db:
            run_service.update_run_status(db, run_id, str(state.status), state.exit_reason)
            if starting_molecules:
                run_service.update_run_molecules(db, run_id, starting_molecules)

        # Invalidate cache since run data changed
        cache_service.invalidate_run(run_id)
        cache_service.invalidate_user_runs_list(user_id)

        _append_log(run_id, {
            "timestamp": datetime.now().isoformat(),
            "message": f"Workflow completed with status {state.status}",
            "level": "INFO",
        })

        # Mark experiment as completed
        experiment.mark_completed()
        experiment_logger.update_experiment(experiment)

        # Log completion
        audit_logger.log(
            event_type=AuditEventType.EXPERIMENT_COMPLETED,
            message=f"Completed experiment {experiment_id} for run {run_id}",
            user_id=user_id,
            username=username,
            experiment_id=experiment_id,
            run_id=run_id,
            details={
                "status": str(state.status),
                "molecules_evaluated": len(state.experimental_results)
            }
        )

    except Exception as e:
        # Mark experiment as failed
        error = ExperimentError(
            type=type(e).__name__,
            message=str(e),
            traceback="",  # Don't include full traceback for security
        )
        experiment.mark_failed(error)
        experiment_logger.update_experiment(experiment)

        # Update database
        with db_context as db:
            run_service.update_run_status(db, run_id, "failed", str(e))

        # Invalidate cache
        cache_service.invalidate_run(run_id)
        cache_service.invalidate_user_runs_list(user_id)

        # Log error
        _append_log(run_id, {
            "timestamp": datetime.now().isoformat(),
            "message": f"Workflow failed: {str(e)}",
            "level": "ERROR",
        })

        # Log failure
        audit_logger.log(
            event_type=AuditEventType.EXPERIMENT_FAILED,
            message=f"Experiment {experiment_id} failed for run {run_id}",
            user_id=user_id,
            username=username,
            experiment_id=experiment_id,
            run_id=run_id,
            severity=AuditSeverity.ERROR,
            details={"error": str(e)}
        )


# ==================== API Endpoints ====================

@router.post("", response_model=RunInfo)
async def create_run(
    req: RunCreateRequest,
    background: BackgroundTasks,
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: DBSession = Depends(get_db)
):
    """
    Create and start a new optimization run.

    Requires authentication.
    """
    # Include microseconds to avoid collisions
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    paths = ensure_run_dirs(run_id)
    (Path(paths["inputs"]) / "prompt.txt").write_text(req.prompt)
    note = req.note.strip() if req.note else None
    if note:
        (Path(paths["inputs"]) / "note.txt").write_text(note)

    # Create run in database
    run_model = run_service.create_run(
        db=db,
        run_id=run_id,
        user_id=str(current_user.id),
        prompt=req.prompt,
        max_iterations=req.max_iterations,
        batch_size=req.batch_size,
        note=note,
        metadata={"paths": paths}
    )

    # Update status to running
    run_service.update_run_status(db, run_id, "running")

    # Create RunInfo for response
    info = run_service.run_to_info(run_model, paths=paths)
    info.username = current_user.username

    # Cache the run for fast subsequent access
    cache_service.cache_run(run_id, info.model_dump())

    # Create experiment record
    experiment = experiment_logger.create_experiment(
        run_id=run_id,
        session_id="",
        user_id=str(current_user.id),
        username=current_user.username,
        prompt=req.prompt,
        parameters={
            "max_iterations": req.max_iterations,
            "batch_size": req.batch_size,
        },
        notes=note,
    )

    # Log in audit trail
    audit_logger.log(
        event_type=AuditEventType.EXPERIMENT_CREATED,
        message=f"Created experiment for run {run_id}",
        user_id=str(current_user.id),
        username=current_user.username,
        experiment_id=experiment.id,
        run_id=run_id,
        ip_address=request.client.host if request.client else None,
        details={"prompt": req.prompt[:100]}
    )

    background.add_task(
        _run_workflow_background,
        run_id,
        req.prompt,
        req.max_iterations,
        req.batch_size,
        experiment.id,
        str(current_user.id),
        current_user.username
    )
    return info


@router.get("", response_model=RunList)
def list_runs(
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
    limit: int = 100,
    offset: int = 0
):
    """List all runs for the current user with Redis caching."""
    user_id = str(current_user.id)

    # Try cache first (only for first page with default limit)
    if offset == 0 and limit == 100:
        cached_runs = cache_service.get_cached_user_runs_list(user_id)
        if cached_runs:
            # Convert cached data back to RunInfo objects
            user_runs = [RunInfo(**run_data) for run_data in cached_runs]
            return RunList(runs=user_runs)

    # Cache miss or non-default pagination - fetch from database
    db_runs = run_service.list_runs(db, user_id, limit=limit, offset=offset)

    # Convert to RunInfo
    user_runs = []
    for run_model in db_runs:
        paths = run_model.metadata.get("paths", {}) if run_model.metadata else {}
        info = run_service.run_to_info(
            run_model,
            summary_available=Path(summary_txt_path(run_model.id)).exists() if paths else False,
            results_available=Path(results_json_path(run_model.id)).exists() if paths else False,
            paths=paths
        )
        info.username = current_user.username
        user_runs.append(info)

    sorted_runs = sorted(user_runs, key=lambda r: r.created_at, reverse=True)

    # Cache the result for first page
    if offset == 0 and limit == 100:
        cache_service.cache_user_runs_list(
            user_id,
            [run.model_dump() for run in sorted_runs]
        )

    return RunList(runs=sorted_runs)


@router.get("/{run_id}", response_model=RunInfo)
def get_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    """Get detailed information about a specific run."""
    info = check_run_authorization(run_id, current_user, db)
    return info


@router.get("/{run_id}/events")
def sse_events(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    """Server-Sent Events stream for run progress."""
    check_run_authorization(run_id, current_user, db)

    q: "queue.Queue[Dict[str, Any]]" = queue.Queue()

    def push(evt: Dict[str, Any]):
        q.put(evt)

    _SUBSCRIBERS.setdefault(run_id, []).append(push)

    def stream():
        try:
            # Send hello event
            yield f"event: hello\ndata: {{\"run_id\": \"{run_id}\"}}\n\n"
            while True:
                evt = q.get()
                yield f"data: {json.dumps(evt)}\n\n"
        finally:
            # Remove subscriber
            subs = _SUBSCRIBERS.get(run_id, [])
            if push in subs:
                subs.remove(push)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.delete("/{run_id}")
def delete_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    """Delete a run and all its associated data."""
    info = check_run_authorization(run_id, current_user, db)

    # Log deletion
    audit_logger.log(
        event_type=AuditEventType.DATA_DELETE,
        message=f"User {current_user.username} deleted run {run_id}",
        user_id=str(current_user.id),
        username=current_user.username,
        run_id=run_id,
        resource_type="run",
        resource_id=run_id
    )

    # Delete from database
    run_service.delete_run(db, run_id, str(current_user.id))

    # Invalidate caches
    cache_service.invalidate_run(run_id)
    cache_service.invalidate_user_runs_list(str(current_user.id))

    # Delete files
    base = run_dir(run_id)
    if base.exists():
        shutil.rmtree(base)

    return {"deleted": True}


@router.get("/{run_id}/checkpoints")
def list_checkpoints(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    """List all checkpoints for a run."""
    check_run_authorization(run_id, current_user, db)

    base = run_dir(run_id) / "checkpoints"
    if not base.exists():
        return []
    items = sorted([p.name for p in base.glob("*") if p.is_file()])
    return items


@router.get("/{run_id}/checkpoints/{filename:path}")
def download_checkpoint(
    run_id: str,
    filename: str,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    """Download a specific checkpoint file."""
    check_run_authorization(run_id, current_user, db)

    base = (run_dir(run_id) / "checkpoints").resolve()
    target = (base / filename).resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(400, "Invalid checkpoint path")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "Checkpoint not found")

    # Log checkpoint access
    audit_logger.log(
        event_type=AuditEventType.DATA_READ,
        message=f"User {current_user.username} downloaded checkpoint {filename} from run {run_id}",
        user_id=str(current_user.id),
        username=current_user.username,
        run_id=run_id,
        resource_type="checkpoint",
        resource_id=filename
    )

    return FileResponse(str(target), filename=target.name)


@router.get("/{run_id}/artifacts/results.json")
def get_results(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    """Get results JSON for a completed run."""
    check_run_authorization(run_id, current_user, db)

    p = results_json_path(run_id)
    if not p.exists():
        raise HTTPException(404, "Results not found")
    return FileResponse(str(p), media_type="application/json")


@router.get("/{run_id}/artifacts/summary.txt")
def get_summary(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    """Get summary text for a completed run."""
    check_run_authorization(run_id, current_user, db)

    p = summary_txt_path(run_id)
    if not p.exists():
        raise HTTPException(404, "Summary not found")
    return FileResponse(str(p), media_type="text/plain")


@router.get("/{run_id}/logs")
def get_logs(
    run_id: str,
    limit: int = Query(200, ge=1, le=2000),
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    """Get logs for a run."""
    check_run_authorization(run_id, current_user, db)

    path = run_dir(run_id) / "logs" / "logs.ndjson"
    if not path.exists():
        raise HTTPException(404, "Logs not found")

    events: Deque[Dict[str, Any]] = deque(maxlen=limit)
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                events.append({"message": line})

    return {"events": list(events)}
