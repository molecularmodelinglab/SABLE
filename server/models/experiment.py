"""Experiment models for scientific tracking and reproducibility."""

from datetime import datetime, timezone
from typing import Optional, List
import uuid

from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, Index, Text, Integer, BigInteger
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import relationship

from server.database import Base


class Experiment(Base):
    """Comprehensive experiment tracking model."""

    __tablename__ = "experiments"

    id = Column(String(100), primary_key=True)  # e.g., "exp_abc123"
    run_id = Column(String(100), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False)

    workflow_name = Column(String(100), nullable=False, default="molecular_optimization")
    status = Column(String(50), nullable=False, index=True)  # pending, running, success, failed, cancelled

    prompt = Column(Text, nullable=False)
    parameters = Column(JSON, nullable=False, default=dict)
    parsed_arguments = Column(JSON, nullable=False, default=dict)
    targets = Column(JSON, nullable=False, default=list)

    result = Column(JSON, nullable=True)
    best_molecules = Column(JSON, nullable=False, default=list)
    summary = Column(Text, nullable=True)

    error = Column(JSON, nullable=True)
    metrics = Column(JSON, nullable=False, default=dict)
    environment = Column(JSON, nullable=False, default=dict)
    git_commit = Column(String(255), nullable=True)

    notes = Column(Text, nullable=True)
    parent_experiment_id = Column(String(100), nullable=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    extra_metadata = Column(JSON, nullable=False, default=dict)

    # Relationships
    user = relationship("User", back_populates="experiments")
    session = relationship("Session", back_populates="experiments")
    run = relationship("Run", back_populates="experiments")
    logs = relationship("ExperimentLog", back_populates="experiment", cascade="all, delete-orphan")

    __table_args__ = (
        Index('idx_experiments_run_id', 'run_id'),
        Index('idx_experiments_user_id', 'user_id'),
        Index('idx_experiments_status', 'status'),
        Index('idx_experiments_created_at', 'created_at'),
    )

    def __repr__(self):
        return f"<Experiment(id={self.id}, run_id={self.run_id}, status={self.status})>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "run_id": self.run_id,
            "user_id": str(self.user_id),
            "session_id": str(self.session_id),
            "workflow_name": self.workflow_name,
            "status": self.status,
            "prompt": self.prompt,
            "parameters": self.parameters,
            "parsed_arguments": self.parsed_arguments,
            "targets": self.targets,
            "result": self.result,
            "best_molecules": self.best_molecules,
            "summary": self.summary,
            "error": self.error,
            "metrics": self.metrics,
            "environment": self.environment,
            "git_commit": self.git_commit,
            "notes": self.notes,
            "parent_experiment_id": self.parent_experiment_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "metadata": self.extra_metadata,
        }


class ExperimentLog(Base):
    """Detailed logs for experiments."""

    __tablename__ = "experiment_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id = Column(String(100), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False, index=True)

    timestamp = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    level = Column(String(20), nullable=False, default="INFO", index=True)
    message = Column(Text, nullable=False)
    node = Column(String(100), nullable=True)
    iteration = Column(Integer, nullable=True)

    data = Column(JSON, nullable=False, default=dict)

    # Relationships
    experiment = relationship("Experiment", back_populates="logs")

    __table_args__ = (
        Index('idx_exp_logs_experiment_id', 'experiment_id'),
        Index('idx_exp_logs_timestamp', 'timestamp'),
        Index('idx_exp_logs_level', 'level'),
    )

    def __repr__(self):
        return f"<ExperimentLog(id={self.id}, experiment_id={self.experiment_id}, level={self.level})>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "experiment_id": self.experiment_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "level": self.level,
            "message": self.message,
            "node": self.node,
            "iteration": self.iteration,
            "data": self.data,
        }
