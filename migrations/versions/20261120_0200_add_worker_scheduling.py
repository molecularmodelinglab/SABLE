"""Share bounded worker slots and durable remote job controls across processes."""
from alembic import op

revision = 'a2c4e6f80913'
down_revision = 'f1b3c5d7e902'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE sable_campaigns.execution_slots (
        resource TEXT NOT NULL, slot INTEGER NOT NULL, token TEXT NOT NULL,
        expires DOUBLE PRECISION NOT NULL, PRIMARY KEY(resource,slot))''')
    op.execute('ALTER TABLE sable_campaigns.remote_jobs ADD COLUMN requested_operation TEXT')


def downgrade():
    op.execute('ALTER TABLE sable_campaigns.remote_jobs DROP COLUMN requested_operation')
    op.execute('DROP TABLE sable_campaigns.execution_slots')
