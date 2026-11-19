"""Advance explicitly started campaigns independently of browser sessions."""
import time
from .campaigns import CampaignConflict
from .jobs import JobConflict


class CampaignRunner:
    def __init__(self, service, model_factory, clock=time.time):
        self.service, self.model_factory, self.clock = service, model_factory, clock

    def tick(self):
        ledger = self.service.ledger
        with ledger.connect() as db:
            status = "a.state::jsonb->>'status'" if ledger.postgres else "json_extract(a.state, '$.status')"
            accounts = 'JOIN public.users u ON u.id=c.owner AND u.is_active' if ledger.postgres else ''
            candidates = db.execute(f'''SELECT c.id,c.owner FROM campaigns c JOIN agent_campaigns a ON c.id=a.id {accounts}
                WHERE a.execution_mode='running' AND a.next_run_at<=?
                AND ({status} IN ('ready','waiting_jobs') OR a.busy=1) ORDER BY a.last_run_at LIMIT 32''', (self.clock(),)).fetchall()
        for row in candidates:
            campaign_id, owner = row['id'], str(row['owner'])
            with ledger.connect() as db:
                db.begin_write()
                ledger.owned(db, campaign_id, owner)
                execution = db.execute('SELECT execution_mode,next_run_at FROM agent_campaigns WHERE id=?', (campaign_id,)).fetchone()
                if execution['execution_mode'] != 'running' or execution['next_run_at'] > self.clock():
                    continue
                db.execute('UPDATE agent_campaigns SET next_run_at=?,last_run_at=? WHERE id=?',
                           (self.clock()+5, self.clock(), campaign_id))
            try:
                state = self.service.get(campaign_id, owner)
                if state['busy'] or state['execution_mode'] != 'running':
                    return
                if state['status'] == 'waiting_jobs':
                    due = next((job for job in state['jobs'] if job['status'] in {'queued','submitting','running','cancelling'}
                                and job['next_poll'] <= self.clock()), None)
                    if due:
                        self.service.job_operation(campaign_id, due['id'], owner, 'poll')
                    return
                if state['status'] != 'ready':
                    with ledger.connect() as db:
                        db.begin_write()
                        ledger.owned(db, campaign_id, owner)
                        # Preserve a concurrently requested pause. A reply re-enables the schedule.
                        db.execute("UPDATE agent_campaigns SET next_run_at=? WHERE id=?", (self.clock()+30, campaign_id))
                    return
                model = self.model_factory()
                try:
                    self.service.advance(campaign_id, owner, model, scheduled=True)
                finally:
                    if hasattr(model, 'close'):
                        model.close()
            except (CampaignConflict, JobConflict) as exc:
                if 'in progress' in str(exc) or 'current state' in str(exc) or 'superseded' in str(exc):
                    return
                self._pause(campaign_id, owner, str(exc))
            except Exception:
                self._pause(campaign_id, owner, 'Campaign could not continue. Review its activity and configuration before resuming.')
            return

    def _pause(self, campaign_id, owner, message):
        with self.service.ledger.connect() as db:
            db.begin_write()
            self.service.ledger.owned(db, campaign_id, owner)
            # Another runner may own a live operation. Do not interfere with its lease.
            changed = db.execute("UPDATE agent_campaigns SET execution_mode='paused',execution_error=? WHERE id=? AND busy=0 AND execution_mode='running'", (message, campaign_id))
            if changed.rowcount:
                self.service.ledger.event(db, campaign_id, 'campaign', 'campaign_paused', {'reason': message, 'source': 'scheduler'})
