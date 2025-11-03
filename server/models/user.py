"""User model for authentication and user management."""

from datetime import datetime, timezone
from typing import Optional
import uuid

from sqlalchemy import Column, String, Boolean, DateTime, JSON, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from server.database import Base


class User(Base):
    """User account model."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=True)  # NULL if using Auth0
    auth_provider = Column(String(50), nullable=False, default="local")  # 'local' or 'auth0'
    auth0_user_id = Column(String(255), nullable=True, index=True)

    is_active = Column(Boolean, nullable=False, default=True)
    is_verified = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, nullable=False, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))
    last_login = Column(DateTime, nullable=True)

    extra_metadata = Column(JSON, nullable=False, default=dict)

    # Relationships
    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")
    runs = relationship("Run", back_populates="user", cascade="all, delete-orphan")
    experiments = relationship("Experiment", back_populates="user", cascade="all, delete-orphan")
    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")
    audit_events = relationship("AuditEvent", back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        Index('idx_users_email', 'email'),
        Index('idx_users_auth0_id', 'auth0_user_id'),
    )

    def __repr__(self):
        return f"<User(id={self.id}, username={self.username}, email={self.email})>"

    def to_dict(self):
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "email": self.email,
            "username": self.username,
            "auth_provider": self.auth_provider,
            "is_active": self.is_active,
            "is_verified": self.is_verified,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "extra_metadata": self.extra_metadata,
        }
