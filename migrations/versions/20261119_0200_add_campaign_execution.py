"""Persist campaign execution intent and fair background scheduling."""
from alembic import op

revision = 'e8c2a94d601b'
down_revision = 'd7e9a31c502f'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE sable_campaigns.agent_campaigns ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'manual'")
    op.execute('ALTER TABLE sable_campaigns.agent_campaigns ADD COLUMN next_run_at DOUBLE PRECISION NOT NULL DEFAULT 0')
    op.execute('ALTER TABLE sable_campaigns.agent_campaigns ADD COLUMN last_run_at DOUBLE PRECISION NOT NULL DEFAULT 0')
    op.execute('ALTER TABLE sable_campaigns.agent_campaigns ADD COLUMN execution_error TEXT')
    op.execute('CREATE INDEX campaign_execution_due_idx ON sable_campaigns.agent_campaigns(execution_mode, next_run_at, last_run_at)')


def downgrade():
    op.execute('DROP INDEX sable_campaigns.campaign_execution_due_idx')
    for column in ('execution_error', 'last_run_at', 'next_run_at', 'execution_mode'):
        op.execute(f'ALTER TABLE sable_campaigns.agent_campaigns DROP COLUMN {column}')
