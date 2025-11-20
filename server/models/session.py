"""Session models for authentication and session management."""

from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid

from sqlalchemy import Column, String, Boolean, DateTime, JSON, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, INET
from sqlalchemy.orm import relationship

from server.database import Base


class Session(Base):
    """User session model for tracking active sessions."""

    __tablename__ = "sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token = Column(String(255), unique=True, nullable=False, index=True)

    ip_address = Column(INET, nullable=True)
    user_agent = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    last_activity = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)

    is_active = Column(Boolean, nullable=False, default=True)
    extra_metadata = Column(JSON, nullable=False, default=dict)

    # Relationships
    user = relationship("User", back_populates="sessions")
    runs = relationship("Run", back_populates="session")
    experiments = relationship("Experiment", back_populates="session")
    conversations = relationship("Conversation", back_populates="session")
    audit_events = relationship("AuditEvent", back_populates="session")

    __table_args__ = (
        Index('idx_sessions_token', 'token'),
        Index('idx_sessions_user_id', 'user_id'),
        Index('idx_sessions_expires_at', 'expires_at'),
    )

    def __repr__(self):
        return f"<Session(id={self.id}, user_id={self.user_id}, expires_at={self.expires_at})>"

    def is_expired(self) -> bool:
        """Check if session has expired."""
        expires_at = self.expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expires_at if expires_at else True

    def refresh(self, hours: int = 24):
        """Refresh session expiration."""
        self.last_activity = datetime.now(timezone.utc)
        self.expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "ip_address": str(self.ip_address) if self.ip_address else None,
            "created_at": self._iso_or_none(self.created_at),
            "last_activity": self._iso_or_none(self.last_activity),
            "expires_at": self._iso_or_none(self.expires_at),
            "is_active": self.is_active,
            "is_expired": self.is_expired(),
            "extra_metadata": self.extra_metadata,
        }

    @staticmethod
    def _iso_or_none(value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class SessionToken(Base):
    """Deprecated: Kept for backward compatibility. Sessions now use the token directly."""

    __tablename__ = "session_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token = Column(String(255), unique=True, nullable=False, index=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)

    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=False)

    def __repr__(self):
        return f"<SessionToken(session_id={self.session_id}, expires_at={self.expires_at})>"
