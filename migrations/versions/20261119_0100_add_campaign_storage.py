"""Store scientific campaigns alongside their user accounts in PostgreSQL."""
from alembic import op

revision = 'd7e9a31c502f'
down_revision = '9c5d2e71b804'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE SCHEMA sable_campaigns')
    op.execute('''CREATE TABLE sable_campaigns.campaigns (
        id TEXT PRIMARY KEY,
        owner UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
        max_actions INTEGER NOT NULL CHECK (max_actions > 0),
        max_units INTEGER NOT NULL CHECK (max_units > 0),
        used_actions INTEGER NOT NULL DEFAULT 0 CHECK (used_actions >= 0),
        used_units INTEGER NOT NULL DEFAULT 0 CHECK (used_units >= 0))''')
    op.execute('CREATE INDEX campaign_owner_idx ON sable_campaigns.campaigns(owner)')
    op.execute('''CREATE TABLE sable_campaigns.actions (
        campaign TEXT NOT NULL REFERENCES sable_campaigns.campaigns(id) ON DELETE CASCADE,
        id TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
        PRIMARY KEY(campaign, id))''')
    op.execute('''CREATE TABLE sable_campaigns.events (
        sequence BIGSERIAL PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
        campaign TEXT NOT NULL REFERENCES sable_campaigns.campaigns(id) ON DELETE CASCADE,
        action TEXT NOT NULL, kind TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL)''')
    op.execute('CREATE INDEX campaign_event_cursor_idx ON sable_campaigns.events(campaign, sequence)')
    op.execute('''CREATE TABLE sable_campaigns.agent_campaigns (
        id TEXT PRIMARY KEY REFERENCES sable_campaigns.campaigns(id) ON DELETE CASCADE,
        state TEXT NOT NULL, busy INTEGER NOT NULL DEFAULT 0 CHECK (busy IN (0,1)),
        lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
        created_sequence BIGSERIAL NOT NULL UNIQUE)''')
    op.execute('''CREATE TABLE sable_campaigns.campaign_artifacts (
        id TEXT PRIMARY KEY,
        campaign TEXT NOT NULL REFERENCES sable_campaigns.campaigns(id) ON DELETE CASCADE,
        name TEXT NOT NULL, content TEXT NOT NULL, size_bytes INTEGER NOT NULL,
        sha256 TEXT NOT NULL, created_sequence BIGSERIAL NOT NULL UNIQUE)''')
    op.execute('CREATE INDEX campaign_artifact_idx ON sable_campaigns.campaign_artifacts(campaign, created_sequence)')
    op.execute('''CREATE TABLE sable_campaigns.remote_jobs (
        id TEXT PRIMARY KEY, campaign TEXT NOT NULL, action TEXT NOT NULL,
        skill_id TEXT NOT NULL, version TEXT NOT NULL, digest TEXT NOT NULL,
        name TEXT NOT NULL, payload TEXT NOT NULL, binding TEXT NOT NULL,
        status TEXT NOT NULL, remote_id TEXT, result TEXT, error TEXT,
        created DOUBLE PRECISION NOT NULL, updated DOUBLE PRECISION NOT NULL,
        deadline DOUBLE PRECISION NOT NULL, next_poll DOUBLE PRECISION NOT NULL,
        lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
        token TEXT, diagnostics TEXT, credential_ref TEXT,
        UNIQUE(campaign, action),
        FOREIGN KEY(campaign, action) REFERENCES sable_campaigns.actions(campaign, id) ON DELETE CASCADE)''')
    op.execute('CREATE INDEX campaign_job_due_idx ON sable_campaigns.remote_jobs(status, next_poll, lease_until)')


def downgrade():
    for table in ('remote_jobs', 'campaign_artifacts', 'agent_campaigns', 'events', 'actions', 'campaigns'):
        op.execute(f'DROP TABLE sable_campaigns.{table}')
    op.execute('DROP SCHEMA sable_campaigns')
