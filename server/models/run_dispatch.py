"""Durable global admission state and per-run publication/execution leases."""

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, func

from server.database import Base


class SchedulerState(Base):
    __tablename__ = "run_scheduler_state"

    id = Column(Integer, primary_key=True, autoincrement=False)
    max_concurrent = Column(Integer, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_run_scheduler_singleton"),
        CheckConstraint("max_concurrent > 0", name="ck_run_scheduler_positive_limit"),
    )


class RunDispatch(Base):
    __tablename__ = "run_dispatches"

    run_id = Column(String(100), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    state = Column(String(20), nullable=False, default="pending", server_default="pending")
    dispatch_token = Column(String(36), nullable=True)
    reservation_deadline = Column(DateTime(timezone=True), nullable=True)
    publication_owner = Column(String(36), nullable=True)
    publication_lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    publish_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    execution_owner = Column(String(36), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'reserved', 'dispatched', 'running', 'finished', 'failed')",
            name="ck_run_dispatch_state",
        ),
        Index("ix_run_dispatches_state_next_attempt", "state", "next_attempt_at"),
    )
