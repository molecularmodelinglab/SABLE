"""add run reference to conversations

Revision ID: 3c2d9c45b6f8
Revises: 7f1a6a2c3e5b
Create Date: 2025-11-04 15:35:00.000000

"""
from typing import Sequence, Union

from alembic import op  # type: ignore
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3c2d9c45b6f8"
down_revision: Union[str, None] = "7f1a6a2c3e5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("run_id", sa.String(length=100), nullable=True))
    op.create_index("ix_conversations_run_id", "conversations", ["run_id"], unique=False)
    op.create_foreign_key(
        "fk_conversations_run_id",
        "conversations",
        "runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_conversations_run_id", "conversations", type_="foreignkey")
    op.drop_index("ix_conversations_run_id", table_name="conversations")
    op.drop_column("conversations", "run_id")
