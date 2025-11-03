"""Audit event models for security and compliance tracking."""

from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import Column, String, Boolean, DateTime, JSON, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID, INET
from sqlalchemy.orm import relationship

from server.database import Base


class AuditEvent(Base):
    """Audit log for tracking all security-relevant events."""

    __tablename__ = "audit_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    timestamp = Column(DateTime, nullable=False, default=datetime.now(datetime.timezone.utc), index=True)
    event_type = Column(String(100), nullable=False, index=True)
    # Event types: USER_LOGIN, USER_LOGOUT, USER_REGISTER, EXPERIMENT_CREATED,
    # EXPERIMENT_STARTED, EXPERIMENT_COMPLETED, EXPERIMENT_FAILED,
    # UNAUTHORIZED_ACCESS, DATA_READ, DATA_WRITE, DATA_DELETE, etc.

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True)

    ip_address = Column(INET, nullable=True)
    user_agent = Column(Text, nullable=True)

    message = Column(Text, nullable=False)
    severity = Column(String(20), nullable=False, default="INFO", index=True)
    # Severity levels: DEBUG, INFO, WARNING, ERROR, CRITICAL

    success = Column(Boolean, nullable=False, default=True)

    resource_type = Column(String(100), nullable=True)  # run, experiment, user, etc.
    resource_id = Column(String(255), nullable=True)

    run_id = Column(String(100), nullable=True)
    experiment_id = Column(String(100), nullable=True)

    error_message = Column(Text, nullable=True)
    details = Column(JSON, nullable=False, default=dict)

    # Relationships
    user = relationship("User", back_populates="audit_events")
    session = relationship("Session", back_populates="audit_events")

    __table_args__ = (
        Index('idx_audit_timestamp', 'timestamp'),
        Index('idx_audit_user_id', 'user_id'),
        Index('idx_audit_event_type', 'event_type'),
        Index('idx_audit_severity', 'severity'),
    )

    def __repr__(self):
        return f"<AuditEvent(id={self.id}, event_type={self.event_type}, user_id={self.user_id})>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "event_type": self.event_type,
            "user_id": str(self.user_id) if self.user_id else None,
            "session_id": str(self.session_id) if self.session_id else None,
            "ip_address": str(self.ip_address) if self.ip_address else None,
            "user_agent": self.user_agent,
            "message": self.message,
            "severity": self.severity,
            "success": self.success,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "run_id": self.run_id,
            "experiment_id": self.experiment_id,
            "error_message": self.error_message,
            "details": self.details,
        }
