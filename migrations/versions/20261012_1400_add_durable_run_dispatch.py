"""Add durable run admission, publication and execution leases.

Revision ID: 9c5d2e71b804
Revises: 4f3b10a792ce
"""

from alembic import op
import sqlalchemy as sa


revision = "9c5d2e71b804"
down_revision = "4f3b10a792ce"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_scheduler_state",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("max_concurrent", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_run_scheduler_singleton"),
        sa.CheckConstraint("max_concurrent > 0", name="ck_run_scheduler_positive_limit"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "run_dispatches",
        sa.Column("run_id", sa.String(100), nullable=False),
        sa.Column("state", sa.String(20), server_default="pending", nullable=False),
        sa.Column("dispatch_token", sa.String(36), nullable=True),
        sa.Column("reservation_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publication_owner", sa.String(36), nullable=True),
        sa.Column("publication_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("publish_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("execution_owner", sa.String(36), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "state IN ('pending', 'reserved', 'dispatched', 'running', 'finished', 'failed')",
            name="ck_run_dispatch_state",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_run_dispatches_state_next_attempt", "run_dispatches", ["state", "next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_run_dispatches_state_next_attempt", table_name="run_dispatches")
    op.drop_table("run_dispatches")
    op.drop_table("run_scheduler_state")
