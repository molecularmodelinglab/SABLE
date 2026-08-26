"""Administrator-only API endpoints providing operational analytics."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from server.auth.dependencies import get_current_admin
from server.database import get_db
from server.schemas.admin import AdminAnalyticsSummary
from server.schemas.boltz_access import AdminBoltzAccessReview, AdminBoltzUserResponse
from server.schemas.run import RunInfo, RunList
from server.services.run_service import run_service
from server.services.cache_service import cache_service
from server.services.admin_service import admin_service
from server.models.user import User
from server.services.boltz_access_service import boltz_access_service
from server.audit import AuditEventType, audit_logger

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users/boltz-access", response_model=list[AdminBoltzUserResponse])
def list_boltz_access_users(
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.created_at.desc()).all()
    responses = [boltz_access_service.as_admin_response(user) for user in users]
    priority = {"pending": 0, "approved": 1, "denied": 2, "not_requested": 3}
    return sorted(responses, key=lambda item: (priority[item.access_status], item.email.lower()))


@router.patch("/users/{user_id}/boltz-access", response_model=AdminBoltzUserResponse)
def review_boltz_access(
    user_id: str,
    payload: AdminBoltzAccessReview,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    response = boltz_access_service.review_request(db, user, admin, payload.status)
    audit_logger.log(
        event_type=AuditEventType.BOLTZ_ACCESS_REVIEWED,
        message=f"Self-hosted Boltz access {payload.status}",
        user_id=str(admin.id),
        username=admin.username,
        resource_type="user",
        resource_id=str(user.id),
        details={"status": payload.status, "subject_email": user.email},
    )
    return response


@router.get("/analytics/summary", response_model=AdminAnalyticsSummary)
async def get_admin_analytics_summary(
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> AdminAnalyticsSummary:
    """Return high-level analytics intended for the administrator dashboard."""
    return admin_service.get_summary(db)


@router.get("/runs", response_model=RunList)
def list_all_runs_for_admin(
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
    limit: int = 100,
    offset: int = 0,
):
    """List runs across all users for administrative inspection."""
    db_runs = run_service.list_runs_for_admin(db, limit=limit, offset=offset)

    user_ids = {str(run.user_id) for run in db_runs if getattr(run, "user_id", None)}
    users_by_id: dict[str, User] = {}
    if user_ids:
        rows = db.query(User).filter(User.id.in_(user_ids)).all()
        for user in rows:
            users_by_id[str(user.id)] = user

    run_infos: list[RunInfo] = []
    for run_model in db_runs:
        info = run_service.run_to_info(run_model)
        user = users_by_id.get(str(run_model.user_id))
        if user:
            info.username = user.username
            info.user_id = str(user.id)
        run_infos.append(info)

    sorted_runs = sorted(run_infos, key=lambda r: r.created_at, reverse=True)
    return RunList(runs=sorted_runs)


@router.get("/runs/{run_id}", response_model=RunInfo)
def get_run_for_admin(
    run_id: str,
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Fetch detailed information about a specific run as an administrator."""
    run_model = run_service.get_run(db, run_id)
    if not run_model:
        raise HTTPException(status_code=404, detail="Run not found")

    info = run_service.run_to_info(run_model)
    if run_model.user_id:
        user = db.query(User).filter(User.id == run_model.user_id).first()
        if user:
            info.username = user.username
            info.user_id = str(user.id)

    cache_service.cache_run(run_id, info.model_dump())
    return info
