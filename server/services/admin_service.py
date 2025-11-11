"""Service layer for administrator analytics and dashboards."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from server.models.user import User
from server.models.run import Run
from server.models.experiment import Experiment
from server.models.session import Session as SessionModel
from server.models.audit import AuditEvent
from server.schemas.admin import (
    AdminAnalyticsSummary,
    AuditEventCount,
    AuditMetrics,
    DailyCount,
    SessionMetrics,
    StatusBreakdown,
    UserMetrics,
)


class AdminAnalyticsService:
    """Aggregates analytics and operational metrics for administrator dashboards."""

    WARNING_SEVERITIES = {"WARNING", "ERROR", "CRITICAL"}

    @staticmethod
    def _ensure_timezone(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def _collect_user_metrics(self, db: Session, now: datetime) -> UserMetrics:
        users: List[User] = db.query(User).all()
        total = len(users)
        active = sum(1 for user in users if user.is_active)
        inactive = total - active
        verified = sum(1 for user in users if user.is_verified)
        admins = sum(1 for user in users if "admin" in user.roles)
        recent_threshold = now - timedelta(days=30)
        recently_active = sum(
            1
            for user in users
            if user.last_login and self._ensure_timezone(user.last_login) >= recent_threshold
        )

        return UserMetrics(
            total=total,
            active=active,
            inactive=inactive,
            verified=verified,
            admins=admins,
            recently_active=recently_active,
        )

    def _collect_status_breakdown(
        self,
        db: Session,
        model,
        created_at_attr,
        since: datetime,
    ) -> StatusBreakdown:
        status_counts: Dict[str, int] = {}
        rows: List[Tuple[str, int]] = (
            db.query(model.status, func.count())
            .group_by(model.status)
            .all()
        )
        total = 0
        for status, count in rows:
            key = (status or "unknown").lower()
            value = int(count or 0)
            status_counts[key] = value
            total += value

        trend_rows = (
            db.query(func.date_trunc("day", created_at_attr).label("day"), func.count())
            .filter(created_at_attr >= since)
            .group_by("day")
            .order_by("day")
            .all()
        )

        daily_series: List[DailyCount] = []
        for day_value, count in trend_rows:
            day_dt = self._ensure_timezone(day_value)
            daily_series.append(DailyCount(date=day_dt, count=int(count or 0)))

        return StatusBreakdown(
            total=total,
            by_status=status_counts,
            last_30_days=daily_series,
        )

    def _collect_session_metrics(self, db: Session, now: datetime) -> SessionMetrics:
        active_sessions_query = db.query(func.count(SessionModel.id)).filter(SessionModel.is_active.is_(True))
        active_sessions = int(active_sessions_query.scalar() or 0)

        expiring_soon_query = (
            db.query(func.count(SessionModel.id))
            .filter(SessionModel.is_active.is_(True))
            .filter(SessionModel.expires_at.isnot(None))
            .filter(SessionModel.expires_at <= now + timedelta(hours=24))
        )
        expiring_soon = int(expiring_soon_query.scalar() or 0)

        durations: List[float] = []
        for created_at, expires_at in db.query(SessionModel.created_at, SessionModel.expires_at).filter(
            SessionModel.is_active.is_(True)
        ):
            created_dt = self._ensure_timezone(created_at)
            expires_dt = self._ensure_timezone(expires_at)
            if created_dt and expires_dt and expires_dt > created_dt:
                durations.append((expires_dt - created_dt).total_seconds())

        average_duration_hours = None
        if durations:
            average_duration_hours = sum(durations) / len(durations) / 3600

        return SessionMetrics(
            active=active_sessions,
            expiring_within_24h=expiring_soon,
            average_duration_hours=average_duration_hours,
        )

    def _collect_audit_metrics(self, db: Session, now: datetime) -> AuditMetrics:
        seven_days_ago = now - timedelta(days=7)
        thirty_days_ago = now - timedelta(days=30)

        total_last_7_days = int(
            db.query(func.count(AuditEvent.id))
            .filter(AuditEvent.timestamp >= seven_days_ago)
            .scalar()
            or 0
        )

        critical_last_7_days = int(
            db.query(func.count(AuditEvent.id))
            .filter(AuditEvent.timestamp >= seven_days_ago)
            .filter(AuditEvent.severity.in_(list(self.WARNING_SEVERITIES)))
            .scalar()
            or 0
        )

        severity_rows = (
            db.query(AuditEvent.severity, func.count(AuditEvent.id))
            .filter(AuditEvent.timestamp >= seven_days_ago)
            .group_by(AuditEvent.severity)
            .all()
        )
        severity_counts: Dict[str, int] = {}
        for severity, count in severity_rows:
            key = (severity or "UNKNOWN").upper()
            severity_counts[key] = int(count or 0)

        top_events_rows = (
            db.query(AuditEvent.event_type, func.count(AuditEvent.id).label("event_count"))
            .filter(AuditEvent.timestamp >= thirty_days_ago)
            .group_by(AuditEvent.event_type)
            .order_by(func.count(AuditEvent.id).desc())
            .limit(5)
            .all()
        )
        top_events: List[AuditEventCount] = []
        for event_type, count in top_events_rows:
            if not event_type:
                continue
            top_events.append(AuditEventCount(event_type=event_type, count=int(count or 0)))

        return AuditMetrics(
            total_last_7_days=total_last_7_days,
            critical_last_7_days=critical_last_7_days,
            last_7_days_by_severity=severity_counts,
            top_events_last_30_days=top_events,
        )

    def get_summary(self, db: Session) -> AdminAnalyticsSummary:
        """Generate the composite analytics summary for administrators."""
        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)

        user_metrics = self._collect_user_metrics(db, now)
        run_metrics = self._collect_status_breakdown(db, Run, Run.created_at, thirty_days_ago)
        experiment_metrics = self._collect_status_breakdown(db, Experiment, Experiment.created_at, thirty_days_ago)
        session_metrics = self._collect_session_metrics(db, now)
        audit_metrics = self._collect_audit_metrics(db, now)

        return AdminAnalyticsSummary(
            generated_at=now,
            users=user_metrics,
            runs=run_metrics,
            experiments=experiment_metrics,
            sessions=session_metrics,
            audit=audit_metrics,
        )


admin_service = AdminAnalyticsService()
