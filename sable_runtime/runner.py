"""Advance started campaigns fairly, with shared execution limits."""
import time
from .campaigns import CampaignConflict
from .jobs import JobConflict
from .scheduling import ExecutionSlots, limit


class CampaignRunner:
    def __init__(self, service, model_factory, clock=time.time):
        self.service, self.model_factory, self.clock = service, model_factory, clock
        self.slots = ExecutionSlots(service.ledger)

    def tick(self, lane='all'):
        ledger = self.service.ledger
        with ledger.connect() as db:
            status = "a.state::jsonb->>'status'" if ledger.postgres else "json_extract(a.state, '$.status')"
            accounts = 'JOIN public.users u ON u.id=c.owner AND u.is_active' if ledger.postgres else ''
            statuses = "('waiting_jobs')" if lane == 'jobs' else "('ready')" if lane == 'models' else "('ready','waiting_jobs')"
            candidates = db.execute(f'''SELECT c.id,c.owner,
                row_number() OVER (PARTITION BY c.owner ORDER BY a.last_run_at,c.id) AS owner_rank,
                (SELECT max(a2.last_run_at) FROM campaigns c2 JOIN agent_campaigns a2 ON c2.id=a2.id WHERE c2.owner=c.owner) AS owner_last
                FROM campaigns c JOIN agent_campaigns a ON c.id=a.id {accounts}
                WHERE a.execution_mode IN ('running','step') AND a.next_run_at<=?
                AND ({status} IN {statuses} OR (a.busy=1 AND a.lease_until<=?))
                ORDER BY owner_rank,owner_last,a.last_run_at,c.id LIMIT 64''', (self.clock(), time.time())).fetchall()
        for row in candidates:
            campaign_id, owner = row['id'], str(row['owner'])
            with self.slots.acquire('owner:'+owner, limit('SABLE_MAX_USER_OPERATIONS', 1)) as owner_slot:
                if not owner_slot:
                    continue
                with self.slots.acquire('campaign:'+campaign_id) as campaign_slot:
                    if not campaign_slot:
                        continue
                    if self._run(campaign_id, owner):
                        return True
        return False

    def _run(self, campaign_id, owner):
        ledger = self.service.ledger
        with ledger.connect() as db:
            db.begin_write()
            ledger.owned(db, campaign_id, owner)
            execution = db.execute('SELECT execution_mode,next_run_at FROM agent_campaigns WHERE id=?', (campaign_id,)).fetchone()
            if execution['execution_mode'] not in {'running','step'} or execution['next_run_at'] > self.clock():
                return False
            step = execution['execution_mode'] == 'step'
            db.execute('UPDATE agent_campaigns SET next_run_at=?,last_run_at=? WHERE id=?',
                       (self.clock()+5, self.clock(), campaign_id))
        advanced = False
        try:
            state = self.service.get(campaign_id, owner)
            if state['busy'] or state['execution_mode'] not in {'running','step'}:
                return False
            if state['status'] == 'waiting_jobs':
                due = next((job for job in state['jobs'] if job['status'] in {'queued','submitting','running','cancelling'}
                            and job['next_poll'] <= self.clock()), None)
                if due:
                    self.service.job_operation(campaign_id, due['id'], owner, 'poll')
                return bool(due)
            if state['status'] != 'ready':
                return False
            model = self.model_factory()
            try:
                self.service.advance(campaign_id, owner, model, scheduled=True)
                advanced = True
            finally:
                if hasattr(model, 'close'):
                    model.close()
        except (CampaignConflict, JobConflict) as exc:
            if 'in progress' in str(exc) or 'current state' in str(exc) or 'superseded' in str(exc):
                return False
            self._pause(campaign_id, owner, str(exc))
        except Exception:
            self._pause(campaign_id, owner, 'Campaign could not continue. Review its activity and configuration before resuming.')
        finally:
            if step and advanced:
                with ledger.connect() as db:
                    db.begin_write()
                    ledger.owned(db, campaign_id, owner)
                    changed = db.execute("UPDATE agent_campaigns SET execution_mode='paused' WHERE id=? AND execution_mode='step'", (campaign_id,))
                    if changed.rowcount:
                        ledger.event(db, campaign_id, 'campaign', 'campaign_paused', {'source':'single_step'})
        return True

    def _pause(self, campaign_id, owner, message):
        with self.service.ledger.connect() as db:
            db.begin_write()
            self.service.ledger.owned(db, campaign_id, owner)
            changed = db.execute("UPDATE agent_campaigns SET execution_mode='paused',execution_error=? WHERE id=? AND busy=0 AND execution_mode IN ('running','step')", (message, campaign_id))
            if changed.rowcount:
                self.service.ledger.event(db, campaign_id, 'campaign', 'campaign_paused', {'reason': message, 'source': 'scheduler'})
