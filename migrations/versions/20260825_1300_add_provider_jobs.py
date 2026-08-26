"""add provider jobs

Revision ID: 4f3b10a792ce
Revises: 8a2e7f0c4d91
Create Date: 2026-08-25 13:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "4f3b10a792ce"
down_revision: Union[str, None] = "8a2e7f0c4d91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "provider_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("execution_kind", sa.String(length=50), nullable=False),
        sa.Column("provider_job_id", sa.String(length=255), nullable=False),
        sa.Column("protein_scope_id", sa.String(length=100), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("completed_items", sa.Integer(), nullable=False),
        sa.Column("failed_items", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["credential_id"], ["provider_credentials.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_job_id", name="uq_provider_job_external_id"),
        sa.UniqueConstraint("run_id", "request_fingerprint", name="uq_provider_job_request"),
    )
    op.create_index("ix_provider_jobs_run_id", "provider_jobs", ["run_id"], unique=False)
    op.create_index("ix_provider_jobs_user_id", "provider_jobs", ["user_id"], unique=False)
    op.create_index("ix_provider_jobs_credential_id", "provider_jobs", ["credential_id"], unique=False)
    op.create_index("ix_provider_jobs_status", "provider_jobs", ["status"], unique=False)

    op.create_table(
        "provider_job_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("molecule_id", sa.String(length=255), nullable=False),
        sa.Column("provider_result_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["provider_job_id"], ["provider_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_job_id", "molecule_id", name="uq_provider_job_result_molecule"),
    )
    op.create_index(
        "ix_provider_job_results_provider_job_id",
        "provider_job_results",
        ["provider_job_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_provider_job_results_provider_job_id", table_name="provider_job_results")
    op.drop_table("provider_job_results")
    op.drop_index("ix_provider_jobs_status", table_name="provider_jobs")
    op.drop_index("ix_provider_jobs_credential_id", table_name="provider_jobs")
    op.drop_index("ix_provider_jobs_user_id", table_name="provider_jobs")
    op.drop_index("ix_provider_jobs_run_id", table_name="provider_jobs")
    op.drop_table("provider_jobs")