"""Run management API endpoints."""

import json
import os
import shutil
import queue
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Callable, Deque
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse, Response
from sqlalchemy.orm import Session as DBSession

from server.database import get_db, get_db_context
from server.models.user import User
from server.auth.dependencies import get_current_user, get_current_active_user
from server.schemas import RunCreateRequest, RunInfo, RunList
from server.storage import (
    ensure_run_dirs,
    results_json_path,
    summary_txt_path,
    run_dir,
    checkpoint_path,
    list_run_checkpoints,
)
from server.services.run_service import run_service
from server.services.cache_service import cache_service
from server.experiment_logger import experiment_logger
from server.audit import audit_logger, AuditEventType, AuditSeverity
from server.models.session import Session as SessionModel
from server.services.run_events import run_event_hub
from server.services.run_scheduler import run_scheduler

router = APIRouter(prefix="/runs", tags=["runs"])

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
    metadata = run_model.extra_metadata or {}
    paths = metadata.get("paths", {}) if isinstance(metadata, dict) else {}
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
# ==================== API Endpoints ====================

@router.post("", response_model=RunInfo)
async def create_run(
    req: RunCreateRequest,
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
    session = db.query(SessionModel).filter(
        SessionModel.user_id == current_user.id,
        SessionModel.is_active == True
    ).order_by(SessionModel.created_at.desc()).first()

    if not session:
        now = datetime.now(timezone.utc)
        session = SessionModel(
            user_id=current_user.id,
            token=f"run-session-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            created_at=now,
            last_activity=now,
            expires_at=now + timedelta(hours=24),
            is_active=True,
            extra_metadata={"source": "run_api"},
        )
        db.add(session)
        db.flush()

    extra_metadata = {
        "paths": paths,
        "max_iterations": req.max_iterations,
        "batch_size": req.batch_size,
    }

    run_model = run_service.create_run(
        db=db,
        run_id=run_id,
        user_id=current_user.id,
        session_id=session.id,
        prompt=req.prompt,
        starting_molecules=[],
        note=note,
        extra_metadata=extra_metadata,
    )

    # Create experiment record
    experiment = experiment_logger.create_experiment(
        run_id=run_id,
        session_id=str(session.id),
        user_id=str(current_user.id),
        username=current_user.username,
        prompt=req.prompt,
        parameters={
            "max_iterations": req.max_iterations,
            "batch_size": req.batch_size,
        },
        notes=note,
    )

    # Persist experiment identifier on the run metadata
    updated_metadata = dict(run_model.extra_metadata or {})
    updated_metadata["experiment_id"] = experiment.id
    run_model.extra_metadata = updated_metadata
    db.commit()
    db.refresh(run_model)

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

    run_scheduler.submit_run(run_id)

    # Refresh run model with latest status
    db.refresh(run_model)

    metadata = run_model.extra_metadata or {}
    paths = metadata.get("paths", {}) if isinstance(metadata, dict) else {}
    info = run_service.run_to_info(run_model, paths=paths)
    info.username = current_user.username

    cache_service.cache_run(run_id, info.model_dump())
    cache_service.invalidate_user_runs_list(str(current_user.id))

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
        metadata = run_model.extra_metadata or {}
        paths = metadata.get("paths", {}) if isinstance(metadata, dict) else {}
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

    if os.getenv("ENVIRONMENT") == "testing":
        return Response(status_code=200)

    q: "queue.Queue[Dict[str, Any]]" = queue.Queue()

    def push(evt: Dict[str, Any]) -> None:
        q.put(evt)

    run_event_hub.subscribe(run_id, push)

    def stream():
        try:
            # Send hello event
            yield f"event: hello\ndata: {{\"run_id\": \"{run_id}\"}}\n\n"
            while True:
                evt = q.get()
                yield f"data: {json.dumps(evt)}\n\n"
        finally:
            run_event_hub.unsubscribe(run_id, push)

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
    return list_run_checkpoints(run_id)


@router.get("/{run_id}/checkpoints/{filename:path}")
def download_checkpoint(
    run_id: str,
    filename: str,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db)
):
    """Download a specific checkpoint file."""
    check_run_authorization(run_id, current_user, db)

    try:
        target = checkpoint_path(run_id, filename)
    except FileNotFoundError:
        raise HTTPException(404, "Checkpoint not found")
    except ValueError:
        raise HTTPException(400, "Invalid checkpoint path")

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
