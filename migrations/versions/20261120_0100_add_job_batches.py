"""Allow independently recoverable remote jobs within one campaign action."""
from alembic import op
revision = 'f1b3c5d7e902'
down_revision = 'e8c2a94d601b'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE sable_campaigns.remote_jobs ADD COLUMN batch_index INTEGER NOT NULL DEFAULT 0')
    op.execute('ALTER TABLE sable_campaigns.remote_jobs DROP CONSTRAINT remote_jobs_campaign_action_key')
    op.execute('ALTER TABLE sable_campaigns.remote_jobs ADD CONSTRAINT remote_jobs_campaign_action_batch_key UNIQUE(campaign, action, batch_index)')


def downgrade():
    # Refuse downgrade when batches exist, instead of discarding job history.
    op.execute('ALTER TABLE sable_campaigns.remote_jobs ADD CONSTRAINT remote_jobs_campaign_action_key UNIQUE(campaign, action)')
    op.execute('ALTER TABLE sable_campaigns.remote_jobs DROP CONSTRAINT remote_jobs_campaign_action_batch_key')
    op.execute('ALTER TABLE sable_campaigns.remote_jobs DROP COLUMN batch_index')
