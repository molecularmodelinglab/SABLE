"""Administrator-only API endpoints providing operational analytics."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from server.auth.dependencies import get_current_admin
from server.database import get_db
from server.schemas.admin import AdminAnalyticsSummary
from server.services.admin_service import admin_service
from server.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/analytics/summary", response_model=AdminAnalyticsSummary)
async def get_admin_analytics_summary(
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> AdminAnalyticsSummary:
    """Return high-level analytics intended for the administrator dashboard."""
    return admin_service.get_summary(db)
