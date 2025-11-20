"""LIZARD API with new database-backed authentication system."""
import os
from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from datetime import datetime

from server.database import get_db
from sqlalchemy.orm import Session as DBSession
from sqlalchemy import text

from server.models.user import User
from server.auth.dependencies import get_current_user, require_role
from server.experiment_logger import experiment_logger
from server.audit import audit_logger, AuditEventType, AuditSeverity

from server.routers.auth import router as auth_router
from server.routers.conversations import router as conversations_router
from server.routers.runs import router as runs_router
from server.routers.admin import router as admin_router


app = FastAPI(
    title="LIZARD API",
    version="0.2.0",
    description="LIgand optimiZation via Agentic Research and Discovery"
)

# CORS configuration
_default_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://lizard-frontend-dept-lizard-prod.apps.cloudapps.unc.edu",
]

_env_origins = os.getenv("CORS_ORIGINS")
if _env_origins:
    origins = [o.strip() for o in _env_origins.split(",") if o.strip()]
else:
    origins = _default_origins
print(origins)
# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(runs_router)
app.include_router(admin_router)


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
    current_admin: User = Depends(require_role("admin")),
    limit: int = Query(50, ge=1, le=500)
):
    """Get security-related events (admin only)."""
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

    # Count active runs from database
    active_runs_count = 0
    try:
        from server.models.run import Run as RunModel
        active_runs_count = db.query(RunModel).filter(
            RunModel.status == "running"
        ).count()
    except Exception:
        pass

    return {
        "status": "healthy" if (db_healthy and redis_healthy) else "degraded",
        "timestamp": datetime.now().isoformat(),
        "database": "connected" if db_healthy else "disconnected",
        "redis": "connected" if redis_healthy else "disconnected",
        "active_runs": active_runs_count,
    }
