"""Durable provider job and per-molecule result records."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from server.database import Base


class ProviderJob(Base):
    __tablename__ = "provider_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        String(100),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credential_id = Column(
        UUID(as_uuid=True),
        ForeignKey("provider_credentials.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider = Column(String(50), nullable=False)
    execution_kind = Column(String(50), nullable=False)
    provider_job_id = Column(String(255), nullable=False)
    protein_scope_id = Column(String(100), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)
    total_items = Column(Integer, nullable=False, default=0)
    completed_items = Column(Integer, nullable=False, default=0)
    failed_items = Column(Integer, nullable=False, default=0)
    error_code = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    submitted_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_polled_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    run = relationship("Run", back_populates="provider_jobs")
    user = relationship("User", back_populates="provider_jobs")
    credential = relationship("ProviderCredential", back_populates="provider_jobs")
    results = relationship(
        "ProviderJobResult",
        back_populates="provider_job",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("provider", "provider_job_id", name="uq_provider_job_external_id"),
        UniqueConstraint("run_id", "request_fingerprint", name="uq_provider_job_request"),
    )


class ProviderJobResult(Base):
    __tablename__ = "provider_job_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("provider_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    molecule_id = Column(String(255), nullable=False)
    provider_result_id = Column(String(255), nullable=True)
    status = Column(String(20), nullable=False)
    metrics = Column(JSON, nullable=False, default=dict)
    artifact_path = Column(Text, nullable=True)
    warnings = Column(JSON, nullable=False, default=list)
    error_message = Column(Text, nullable=True)
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

    provider_job = relationship("ProviderJob", back_populates="results")

    __table_args__ = (
        UniqueConstraint("provider_job_id", "molecule_id", name="uq_provider_job_result_molecule"),
    )