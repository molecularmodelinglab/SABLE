"""LIZARD API with new database-backed authentication system."""

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from typing import Dict, Any, Callable, Deque, Optional
from pathlib import Path
from datetime import datetime
import os
import platform
import subprocess

from server.database import get_db
from sqlalchemy.orm import Session as DBSession
from sqlalchemy import text

from server.schemas import RunCreateRequest, RunInfo, RunList
from server.storage import ensure_run_dirs, results_json_path, summary_txt_path, run_dir
from server.models.user import User
from server.auth.dependencies import get_current_user, get_current_active_user
from server.experiment_logger import experiment_logger, ExperimentStatus, ExperimentError
from server.audit import audit_logger, AuditEventType, AuditSeverity
from run_workflow import WorkflowRunner

# Import and mount auth router
from server.routers.auth import router as auth_router


app = FastAPI(
    title="LIZARD API",
    version="0.2.0",
    description="LIgand optimiZation via Agentic Research and Discovery"
)

# CORS configuration
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount authentication router
app.include_router(auth_router)

# In-memory storage (TODO: migrate to database in Phase 5)
_RUNS: Dict[str, RunInfo] = {}
_SUBSCRIBERS: Dict[str, list[Callable[[Dict[str, Any]], None]]] = {}


def get_git_commit() -> Optional[str]:
    """Get current git commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def get_environment_info() -> Dict[str, str]:
    """Collect environment information for reproducibility."""
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "hostname": platform.node(),
        "git_commit": get_git_commit() or "unknown",
    }


def check_run_authorization(run_id: str, user: User) -> RunInfo:
    """
    Check if user has access to a run.

    Args:
        run_id: Run identifier
        user: Current authenticated user

    Returns:
        RunInfo if authorized

    Raises:
        HTTPException: If not authorized
    """
    info = _RUNS.get(run_id)
    if not info:
        raise HTTPException(404, "Run not found")

    # Check if user owns this run
    if info.user_id != str(user.id):
        # Log unauthorized access attempt
        audit_logger.log(
            event_type=AuditEventType.UNAUTHORIZED_ACCESS,
            message=f"User {user.username} attempted to access run {run_id}",
            user_id=str(user.id),
            username=user.username,
            run_id=run_id,
            severity=AuditSeverity.WARNING,
            success=False,
            details={"owner_user_id": info.user_id}
        )
        raise HTTPException(403, "Access denied: You can only access your own runs")

    return info


def _append_log(run_id: str, event: Dict[str, Any]):
    """Append event to run logs."""
    log_path = run_dir(run_id) / "logs" / "logs.ndjson"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        import json
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
            info = _RUNS.get(run_id)
            if info:
                info.starting_molecules = normalized
                info.updated_at = datetime.now()

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

        # Update run info
        info = _RUNS.get(run_id)
        if info:
            info.starting_molecules = starting_molecules
            info.status = str(state.status)
            info.exit_reason = state.exit_reason
            info.updated_at = datetime.now()
            info.summary_available = state.summary is not None
            info.results_available = results_path.exists()
            _append_log(run_id, {
                "ts": datetime.now().isoformat(),
                "event": "run_completed",
                "status": info.status
            })

        # Mark experiment as completed
        experiment.mark_completed(state.summary)
        experiment_logger.update_experiment(experiment)

        # Log successful completion
        audit_logger.log(
            event_type=AuditEventType.EXPERIMENT_COMPLETED,
            message=f"Completed experiment {experiment_id} successfully",
            user_id=user_id,
            username=username,
            experiment_id=experiment_id,
            run_id=run_id,
            details={
                "iterations": state.current_iteration,
                "status": str(state.status)
            }
        )

    except Exception as e:
        # Log the error
        error = ExperimentError(
            message=str(e),
            error_type=type(e).__name__,
            stack_trace=__import__("traceback").format_exc(),
        )
        experiment.mark_failed(error)
        experiment_logger.update_experiment(experiment)

        # Audit log the failure
        audit_logger.log(
            event_type=AuditEventType.EXPERIMENT_FAILED,
            message=f"Experiment {experiment_id} failed: {str(e)}",
            user_id=user_id,
            username=username,
            experiment_id=experiment_id,
            run_id=run_id,
            severity=AuditSeverity.ERROR,
            success=False,
            error_message=str(e),
            details={"error_type": type(e).__name__}
        )

        # Update run info
        info = _RUNS.get(run_id)
        if info:
            info.status = "failed"
            info.exit_reason = str(e)
            info.updated_at = datetime.now()
            if 'state' in locals() and getattr(state, "starting_molecules", None):
                starting = getattr(state, "starting_molecules", [])
                if isinstance(starting, (list, tuple, set)):
                    info.starting_molecules = [str(m) for m in starting]
                elif starting:
                    info.starting_molecules = [str(starting)]

        _append_log(run_id, {
            "ts": datetime.now().isoformat(),
            "event": "run_failed",
            "error": str(e)
        })

        raise


# ==================== Run Management Endpoints ====================

@app.post("/runs", response_model=RunInfo)
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

    info = RunInfo(
        id=run_id,
        status="running",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        paths=paths,
        note=note,
        user_id=str(current_user.id),
        username=current_user.username,
        starting_molecules=[],
    )
    _RUNS[run_id] = info

    # Create experiment record
    experiment = experiment_logger.create_experiment(
        run_id=run_id,
        session_id="",  # TODO: Get actual session ID
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


@app.get("/runs", response_model=RunList)
def list_runs(current_user: User = Depends(get_current_user)):
    """List all runs for the current user."""
    # Filter runs to only show user's own runs
    user_runs = [
        run for run in _RUNS.values()
        if run.user_id == str(current_user.id)
    ]
    return RunList(runs=sorted(user_runs, key=lambda r: r.created_at, reverse=True))


@app.get("/runs/{run_id}", response_model=RunInfo)
def get_run(run_id: str, current_user: User = Depends(get_current_user)):
    """Get detailed information about a specific run."""
    info = check_run_authorization(run_id, current_user)
    return info


@app.get("/runs/{run_id}/events")
def sse_events(run_id: str, current_user: User = Depends(get_current_user)):
    """Server-Sent Events stream for run progress."""
    check_run_authorization(run_id, current_user)

    import queue

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
                import json
                yield f"data: {json.dumps(evt)}\n\n"
        finally:
            # Remove subscriber
            subs = _SUBSCRIBERS.get(run_id, [])
            if push in subs:
                subs.remove(push)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.delete("/runs/{run_id}")
def delete_run(run_id: str, current_user: User = Depends(get_current_user)):
    """Delete a run and all its associated data."""
    info = check_run_authorization(run_id, current_user)

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

    _RUNS.pop(run_id)
    base = run_dir(run_id)
    if base.exists():
        import shutil
        shutil.rmtree(base)
    return {"deleted": True}


@app.get("/runs/{run_id}/checkpoints")
def list_checkpoints(run_id: str, current_user: User = Depends(get_current_user)):
    """List all checkpoints for a run."""
    check_run_authorization(run_id, current_user)

    base = run_dir(run_id) / "checkpoints"
    if not base.exists():
        return []
    items = sorted([p.name for p in base.glob("*") if p.is_file()])
    return items


@app.get("/runs/{run_id}/checkpoints/{filename:path}")
def download_checkpoint(
    run_id: str,
    filename: str,
    current_user: User = Depends(get_current_user)
):
    """Download a specific checkpoint file."""
    check_run_authorization(run_id, current_user)

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


@app.get("/runs/{run_id}/artifacts/results.json")
def get_results(run_id: str, current_user: User = Depends(get_current_user)):
    """Get results JSON for a completed run."""
    check_run_authorization(run_id, current_user)

    p = results_json_path(run_id)
    if not p.exists():
        raise HTTPException(404, "Results not found")
    return FileResponse(str(p), media_type="application/json")


@app.get("/runs/{run_id}/artifacts/summary.txt")
def get_summary(run_id: str, current_user: User = Depends(get_current_user)):
    """Get summary text for a completed run."""
    check_run_authorization(run_id, current_user)

    p = summary_txt_path(run_id)
    if not p.exists():
        raise HTTPException(404, "Summary not found")
    return FileResponse(str(p), media_type="text/plain")


@app.get("/runs/{run_id}/logs")
def get_logs(
    run_id: str,
    limit: int = Query(200, ge=1, le=2000),
    current_user: User = Depends(get_current_user)
):
    """Get logs for a run."""
    check_run_authorization(run_id, current_user)

    path = run_dir(run_id) / "logs" / "logs.ndjson"
    if not path.exists():
        raise HTTPException(404, "Logs not found")

    import json
    from collections import deque

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


# ==================== Experiment Logging Endpoints ====================

@app.get("/experiments/{experiment_id}")
async def get_experiment(
    experiment_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get detailed experiment information."""
    experiment = experiment_logger.get_experiment(experiment_id)
    if not experiment:
        raise HTTPException(404, "Experiment not found")

    # Check if user has access
    if experiment.user_id != str(current_user.id):
        audit_logger.log(
            event_type=AuditEventType.UNAUTHORIZED_ACCESS,
            message=f"User {current_user.username} attempted to access experiment {experiment_id}",
            user_id=str(current_user.id),
            username=current_user.username,
            experiment_id=experiment_id,
            severity=AuditSeverity.WARNING,
            success=False
        )
        raise HTTPException(403, "Access denied")

    return experiment


@app.get("/experiments")
async def list_experiments(
    current_user: User = Depends(get_current_user),
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500)
):
    """List experiments for the current user."""
    from server.experiment_logger import ExperimentStatus

    status_filter = ExperimentStatus(status) if status else None
    experiments = experiment_logger.search_experiments(
        user_id=str(current_user.id),
        status=status_filter
    )

    return {"experiments": experiments[:limit]}


@app.get("/experiments/run/{run_id}")
async def get_experiment_by_run(
    run_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get experiment information for a specific run."""
    experiments = experiment_logger.get_experiments_by_run(run_id)
    if not experiments:
        raise HTTPException(404, "No experiment found for this run")

    experiment = experiments[0]

    # Check access
    if experiment.user_id != str(current_user.id):
        raise HTTPException(403, "Access denied")

    return experiment


@app.get("/experiments/failed")
async def get_failed_experiments(
    current_user: User = Depends(get_current_user),
    limit: int = Query(20, ge=1, le=100)
):
    """Get all failed experiments for debugging."""
    experiments = experiment_logger.get_failed_experiments(
        user_id=str(current_user.id),
        limit=limit
    )
    return {"experiments": experiments}


# ==================== Audit Logging Endpoints ====================

@app.get("/audit/events")
async def get_audit_events(
    current_user: User = Depends(get_current_user),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000)
):
    """Get audit events for the current user."""
    from server.audit import AuditEventType

    start = datetime.fromisoformat(start_date) if start_date else None
    end = datetime.fromisoformat(end_date) if end_date else None
    evt_type = AuditEventType(event_type) if event_type else None

    events = audit_logger.get_events(
        user_id=str(current_user.id),
        start_date=start,
        end_date=end,
        event_type=evt_type,
        limit=limit
    )

    return {"events": events}


@app.get("/audit/activity")
async def get_user_activity(
    current_user: User = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=500)
):
    """Get activity log for the current user."""
    events = audit_logger.get_user_activity(
        user_id=str(current_user.id),
        limit=limit
    )
    return {"events": events}


@app.get("/audit/security")
async def get_security_events(
    current_user: User = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=500)
):
    """Get security-related events (admin only in production)."""
    # TODO: Add admin check in production
    events = audit_logger.get_security_events(limit=limit)
    return {"events": events}


# ==================== Analytics & Reporting Endpoints ====================

@app.get("/analytics/summary")
async def get_analytics_summary(current_user: User = Depends(get_current_user)):
    """Get summary analytics for the current user."""
    experiments = experiment_logger.get_experiments_by_user(str(current_user.id))

    total_experiments = len(experiments)
    successful = len([e for e in experiments if e.status == "success"])
    failed = len([e for e in experiments if e.status == "failed"])
    running = len([e for e in experiments if e.status == "running"])

    total_molecules_evaluated = sum(e.metrics.molecules_evaluated for e in experiments)
    total_iterations = sum(e.metrics.iterations_completed for e in experiments)

    avg_duration = None
    completed = [e for e in experiments if e.metrics.duration_seconds]
    if completed:
        avg_duration = sum(e.metrics.duration_seconds for e in completed) / len(completed)

    return {
        "user_id": str(current_user.id),
        "username": current_user.username,
        "total_experiments": total_experiments,
        "successful": successful,
        "failed": failed,
        "running": running,
        "total_molecules_evaluated": total_molecules_evaluated,
        "total_iterations": total_iterations,
        "average_duration_seconds": avg_duration,
        "success_rate": successful / total_experiments if total_experiments > 0 else 0,
    }


# ==================== Health Check ====================

@app.get("/health")
async def health_check(db: DBSession = Depends(get_db)):
    """
    Health check endpoint.

    Checks API, database, and Redis connectivity.
    """
    from server.services.cache_service import cache_service

    # Check database
    db_healthy = False
    try:
        db.execute(text("SELECT 1"))
        db_healthy = True
    except Exception:
        pass

    # Check Redis
    redis_healthy = cache_service.is_connected()

    return {
        "status": "healthy" if (db_healthy and redis_healthy) else "degraded",
        "timestamp": datetime.now().isoformat(),
        "database": "connected" if db_healthy else "disconnected",
        "redis": "connected" if redis_healthy else "disconnected",
        "active_runs": len([r for r in _RUNS.values() if r.status == "running"]),
    }
