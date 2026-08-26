"""Encrypted user-owned provider credentials."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from server.database import Base


class ProviderCredential(Base):
    __tablename__ = "provider_credentials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider = Column(String(50), nullable=False, default="boltz_platform")
    name = Column(String(100), nullable=False)
    encrypted_secret = Column(LargeBinary, nullable=False)
    key_hint = Column(String(8), nullable=False)
    status = Column(String(20), nullable=False, default="unverified")
    last_validated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user = relationship("User", back_populates="provider_credentials")
    provider_jobs = relationship("ProviderJob", back_populates="credential")

    __table_args__ = (
        UniqueConstraint("user_id", "provider", "name", name="uq_provider_credential_name"),
    )