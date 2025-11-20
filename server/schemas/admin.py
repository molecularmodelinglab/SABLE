"""Pydantic schemas for administrator analytics endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from pydantic import BaseModel, Field, ConfigDict


class DailyCount(BaseModel):
    """Represents a count of records aggregated by day."""

    date: datetime = Field(..., description="UTC timestamp representing the day of the aggregation")
    count: int = Field(..., ge=0, description="Number of records for the day")


class StatusBreakdown(BaseModel):
    """Breakdown of entities by status with recent trend information."""

    total: int = Field(..., ge=0, description="Total number of entities")
    by_status: Dict[str, int] = Field(default_factory=dict, description="Counts per status value")
    last_30_days: List[DailyCount] = Field(default_factory=list, description="Daily counts for the last 30 days")


class UserMetrics(BaseModel):
    """Key metrics about registered users."""

    total: int = Field(..., ge=0, description="Total number of users")
    active: int = Field(..., ge=0, description="Active user accounts")
    inactive: int = Field(..., ge=0, description="Inactive user accounts")
    verified: int = Field(..., ge=0, description="Accounts with verified email addresses")
    admins: int = Field(..., ge=0, description="Accounts that have the admin role")
    recently_active: int = Field(..., ge=0, description="Users who logged in during the last 30 days")


class SessionMetrics(BaseModel):
    """Metrics summarising active sessions."""

    active: int = Field(..., ge=0, description="Active sessions currently marked as valid")
    expiring_within_24h: int = Field(..., ge=0, description="Active sessions that will expire within the next 24 hours")
    average_duration_hours: float | None = Field(
        None,
        ge=0,
        description="Average intended session duration in hours (created-to-expiry window)",
    )


class AuditEventCount(BaseModel):
    """Count of audit events grouped by event type."""

    event_type: str = Field(..., description="Audit event type identifier")
    count: int = Field(..., ge=0, description="Number of events for this type")


class AuditMetrics(BaseModel):
    """Audit trail statistics for monitoring security posture."""

    total_last_7_days: int = Field(..., ge=0, description="Total audit events generated in the last 7 days")
    critical_last_7_days: int = Field(..., ge=0, description="Audit events with severity WARNING or higher in the last 7 days")
    last_7_days_by_severity: Dict[str, int] = Field(
        default_factory=dict,
        description="Distribution of events by severity for the last 7 days",
    )
    top_events_last_30_days: List[AuditEventCount] = Field(
        default_factory=list,
        description="Most frequent audit events in the last 30 days",
    )


class AdminAnalyticsSummary(BaseModel):
    """Aggregate analytics payload surfaced on the admin dashboard."""

    generated_at: datetime = Field(..., description="Timestamp indicating when the summary was generated")
    users: UserMetrics = Field(..., description="User account statistics")
    runs: StatusBreakdown = Field(..., description="Run pipeline statistics")
    experiments: StatusBreakdown = Field(..., description="Experiment statistics")
    sessions: SessionMetrics = Field(..., description="Authentication session metrics")
    audit: AuditMetrics = Field(..., description="Security and audit insights")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "generated_at": "2025-11-11T12:00:00Z",
                "users": {
                    "total": 42,
                    "active": 38,
                    "inactive": 4,
                    "verified": 36,
                    "admins": 3,
                    "recently_active": 28,
                },
                "runs": {
                    "total": 120,
                    "by_status": {"running": 5, "completed": 95, "failed": 20},
                    "last_30_days": [
                        {"date": "2025-11-01T00:00:00Z", "count": 6},
                        {"date": "2025-11-02T00:00:00Z", "count": 4},
                    ],
                },
                "experiments": {
                    "total": 150,
                    "by_status": {"running": 4, "success": 112, "failed": 34},
                    "last_30_days": [
                        {"date": "2025-11-01T00:00:00Z", "count": 8},
                        {"date": "2025-11-02T00:00:00Z", "count": 5},
                    ],
                },
                "sessions": {
                    "active": 21,
                    "expiring_within_24h": 9,
                    "average_duration_hours": 23.5,
                },
                "audit": {
                    "total_last_7_days": 320,
                    "critical_last_7_days": 12,
                    "last_7_days_by_severity": {"INFO": 200, "WARNING": 90, "ERROR": 30},
                    "top_events_last_30_days": [
                        {"event_type": "USER_LOGIN", "count": 180},
                        {"event_type": "EXPERIMENT_CREATED", "count": 75},
                    ],
                },
            }
        }
    )
