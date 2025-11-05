"""Run models for tracking optimization runs and their logs."""

from datetime import datetime, timezone
from typing import Optional, List
import uuid

from sqlalchemy import Column, String, Boolean, DateTime, JSON, ForeignKey, Index, Text, Integer, BigInteger
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import relationship

from server.database import Base


class Run(Base):
    """Optimization run model."""

    __tablename__ = "runs"

    id = Column(String(100), primary_key=True)  # e.g., "run_20250101_120000"
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False)

    status = Column(String(50), nullable=False, index=True)  # running, completed, failed
    prompt = Column(Text, nullable=False)
    note = Column(Text, nullable=True)

    starting_molecules = Column(ARRAY(Text), nullable=False, default=list)
    exit_reason = Column(Text, nullable=True)

    summary_available = Column(Boolean, nullable=False, default=False)
    results_available = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    extra_metadata = Column(JSON, nullable=False, default=dict)

    # Relationships
    user = relationship("User", back_populates="runs")
    session = relationship("Session", back_populates="runs")
    experiments = relationship("Experiment", back_populates="run", cascade="all, delete-orphan")
    logs = relationship("RunLog", back_populates="run", cascade="all, delete-orphan")
    conversation = relationship("Conversation", back_populates="run", uselist=False)

    __table_args__ = (
        Index('idx_runs_user_id', 'user_id'),
        Index('idx_runs_status', 'status'),
        Index('idx_runs_created_at', 'created_at'),
    )

    def __repr__(self):
        return f"<Run(id={self.id}, user_id={self.user_id}, status={self.status})>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "user_id": str(self.user_id),
            "session_id": str(self.session_id),
            "status": self.status,
            "prompt": self.prompt,
            "note": self.note,
            "starting_molecules": self.starting_molecules,
            "exit_reason": self.exit_reason,
            "summary_available": self.summary_available,
            "results_available": self.results_available,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "metadata": self.extra_metadata,
        }


class RunLog(Base):
    """Logs for optimization runs (replaces NDJSON files)."""

    __tablename__ = "run_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String(100), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)

    timestamp = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    event_type = Column(String(100), nullable=True)
    level = Column(String(20), nullable=False, default="INFO")
    message = Column(Text, nullable=True)
    node = Column(String(100), nullable=True)
    iteration = Column(Integer, nullable=True)

    data = Column(JSON, nullable=False, default=dict)

    # Relationships
    run = relationship("Run", back_populates="logs")

    __table_args__ = (
        Index('idx_run_logs_run_id', 'run_id'),
        Index('idx_run_logs_timestamp', 'timestamp'),
    )

    def __repr__(self):
        return f"<RunLog(id={self.id}, run_id={self.run_id}, level={self.level})>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "run_id": self.run_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "event_type": self.event_type,
            "level": self.level,
            "message": self.message,
            "node": self.node,
            "iteration": self.iteration,
            "data": self.data,
        }
