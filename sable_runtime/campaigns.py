"""Durable, bounded agent campaigns. One model decision per advance."""
import json
import os
import time
from uuid import uuid4

from jsonschema import Draft202012Validator
from .agent_model import AGENT_SPEC, Decision, TOOLS
from .contracts import Action, Budget
from .executor import Executor, ExecutionError
from .ledger import LedgerError, BudgetExceeded
from .jobs import JobManager, ACTIVE
from .artifacts import ArtifactStore


class CampaignConflict(RuntimeError):
    pass


class CampaignService:
    def __init__(self, ledger, registry, allowed, *, services=None, credentials=None, discovery_allowed=None, preferences=None, authorize=None):
        self.preferences = preferences
        self.authorize = authorize
        self.ledger, self.registry = ledger, registry
        self.allowed = frozenset(allowed)  # Host-configured skill/version pairs.
        self.discovery_allowed = frozenset(allowed if discovery_allowed is None else discovery_allowed)
        self.services, self.credentials = dict(services or {}), credentials
        self.jobs = JobManager(ledger, registry, services=self.services, credentials=credentials, allowed=self.allowed, authorize=authorize)
        self.artifacts = ArtifactStore(ledger)
        if ledger.postgres:
            return
        with ledger.connect() as db:
            db.begin_write()
            db.execute('''CREATE TABLE IF NOT EXISTS agent_campaigns (
                id TEXT PRIMARY KEY, state TEXT NOT NULL, busy INTEGER NOT NULL DEFAULT 0)''')
            if 'lease_until' not in {r['name'] for r in db.execute('PRAGMA table_info(agent_campaigns)')}:
                db.execute('ALTER TABLE agent_campaigns ADD COLUMN lease_until REAL NOT NULL DEFAULT 0')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(agent_campaigns)')}
            for name, declaration in [('execution_mode', "TEXT NOT NULL DEFAULT 'manual'"),
                                      ('next_run_at', 'REAL NOT NULL DEFAULT 0'),
                                      ('last_run_at', 'REAL NOT NULL DEFAULT 0'), ('execution_error', 'TEXT')]:
                if name not in columns:
                    db.execute(f'ALTER TABLE agent_campaigns ADD COLUMN {name} {declaration}')

    def create(self, owner, prompt):
        campaign_id = 'campaign-' + uuid4().hex
        preferences = self.preferences(owner) if self.preferences else {}
        pins = self.registry.discover(self._visible_skills(owner))
        state = dict(id=campaign_id, status='ready', history=[{'role': 'user', 'content': prompt}],
                     skills=pins, loaded={}, results={}, report=None, question=None,
                     turns=0, max_turns=100, model=None, pending=None, error=None, agent_spec=AGENT_SPEC, preferences=preferences)
        with self.ledger.connect() as db:
            db.begin_write()
            db.execute('INSERT INTO campaigns(id,owner,max_actions,max_units) VALUES(?,?,?,?)',
                       (campaign_id, owner, 20, 50))
            db.execute("INSERT INTO agent_campaigns(id,state,execution_mode) VALUES(?,?,'draft')", (campaign_id, json.dumps(state)))
            self.ledger.event(db, campaign_id, 'campaign', 'campaign_created', {'prompt': prompt, 'skills': pins})
        return self.get(campaign_id, owner)

    def _read(self, db, campaign_id, owner):
        self.ledger.owned(db, campaign_id, owner)
        row = db.execute('SELECT * FROM agent_campaigns WHERE id=?', (campaign_id,)).fetchone()
        if row is None:
            raise PermissionError('Campaign is not available')
        return json.loads(row['state']), bool(row['busy'])

    def get(self, campaign_id, owner, *, reconcile=True):
        with self.ledger.connect() as db:
            if reconcile:
                db.begin_write()
            state, busy = self._read(db, campaign_id, owner)
            original = json.dumps(state)
            original_busy = busy
            lease = db.execute('SELECT lease_until FROM agent_campaigns WHERE id=?', (campaign_id,)).fetchone()[0]
            if reconcile and busy and lease <= time.time():
                pending = state.get('pending')
                action_id = f"turn-{state['turns']}"
                action = db.execute('SELECT * FROM actions WHERE campaign=? AND id=?', (campaign_id, action_id)).fetchone()
                if pending and pending['name'] == 'execute_operation' and action and action['status'] == 'succeeded':
                    result = json.loads(action['result'])
                    if 'observations' not in result or result['observations']:
                        state['results'][action_id] = result
                    state['history'].append({'role': 'assistant', 'decision': pending,
                                             'result': {'action_id': action_id, 'output': result}})
                    state.update(pending=None, status='ready')
                else:
                    state.update(status='failed', error='interrupted_step_requires_review')
                busy = False
                self.ledger.event(db, campaign_id, action_id, 'campaign_recovered', {'status': state['status']})
            jobs = [self.jobs.public(r) for r in db.execute('SELECT * FROM remote_jobs WHERE campaign=? ORDER BY created', (campaign_id,))]
            cited = set((state.get('report') or {}).get('evidence', []))
            before = {key: state['results'].get(key) for key in cited}
            from .job_results import resolved_job_results
            state['results'].update(resolved_job_results(jobs))
            if reconcile and state['status'] == 'completed' and any(before[key] != state['results'].get(key) for key in cited):
                self.ledger.event(db, campaign_id, 'campaign', 'report_invalidated', {'previous_report': state['report']})
                state.update(status='ready', report=None)
            if not busy and state['status'] in {'ready', 'waiting_jobs'}:
                state['status'] = 'waiting_jobs' if any(j['status'] in ACTIVE for j in jobs) else 'ready'
            if reconcile and (json.dumps(state) != original or busy != original_busy):
                db.execute('UPDATE agent_campaigns SET state=?,busy=? WHERE id=?', (json.dumps(state), int(busy), campaign_id))
            budget = self.ledger.owned(db,campaign_id,owner)
            usage = [json.loads(row['payload']).get('usage',{}) for row in db.execute(
                "SELECT payload FROM events WHERE campaign=? AND kind='model_decision'",(campaign_id,))]
            metrics = {'tool_actions':budget['used_actions'],'max_tool_actions':budget['max_actions'],
                       'operation_units':budget['used_units'],'max_operation_units':budget['max_units'],
                       'input_tokens':sum(u.get('input_tokens',0) for u in usage),
                       'output_tokens':sum(u.get('output_tokens',0) for u in usage)}
            execution = db.execute('SELECT execution_mode,execution_error FROM agent_campaigns WHERE id=?', (campaign_id,)).fetchone()
            return {**state, 'execution_mode': execution['execution_mode'], 'execution_error': execution['execution_error'], 'busy': busy, 'jobs': jobs, 'artifacts': self.artifacts.list(campaign_id, owner), 'metrics':metrics}

    def snapshot(self, campaign_id, owner):
        """Read current results without taking a campaign write lock."""
        return self.get(campaign_id, owner, reconcile=False)

    def revision(self, campaign_id, owner):
        """Return a small durable change cursor for browser refreshes."""
        with self.ledger.connect() as db:
            self.ledger.owned(db, campaign_id, owner)
            return {'revision': db.execute('SELECT coalesce(max(sequence),0) FROM events WHERE campaign=?', (campaign_id,)).fetchone()[0]}

    def _can_access(self, owner, skill_id):
        if self.authorize is None:
            return True
        if owner is None:
            return False
        try:
            self.authorize(owner, skill_id)
        except PermissionError:
            return False
        return True

    def _visible_skills(self, owner):
        if self.authorize is None:
            return self.discovery_allowed
        selected = {key for key in self.discovery_allowed if self._can_access(owner, key[0])}
        return self.registry.available(self.services, candidates=selected)

    def catalog(self, owner=None):
        selected = self._visible_skills(owner)
        return [dict(id=skill.manifest.id, version=skill.manifest.version,
                     description=skill.manifest.description, digest=skill.digest,
                     available=key in selected, resumable=key in self.allowed,
                     requirements=skill.manifest.requirements.model_dump(),
                     operations=[{'id':op.id,'description':op.description} for op in skill.manifest.operations])
                for key, skill in sorted(self.registry._skills.items()) if self._can_access(owner, key[0])]

    def export(self, campaign_id, owner):
        state = self.get(campaign_id, owner)
        contents = {self.artifacts.read(campaign_id,owner,item['id']): item['id'] for item in state['artifacts']}
        def references(value):
            if isinstance(value,str) and value in contents:
                return {'artifact_id':contents[value]}
            if isinstance(value,dict):
                return {key:references(item) for key,item in value.items()}
            if isinstance(value,list):
                return [references(item) for item in value]
            return value
        return references({'schema_version':1, 'kind':'campaign_record', 'campaign':state,
                           'events':self.ledger.events(campaign_id, owner),
                           'notice':'Operational record, not a curated training example. Exact uploaded-file values are replaced by artifact references; review user and tool text before sharing.'})

    def list(self, owner):
        with self.ledger.connect() as db:
            rows = db.execute(f'SELECT a.state,a.busy,a.execution_mode FROM agent_campaigns a JOIN campaigns c ON a.id=c.id WHERE c.owner=? ORDER BY a.{self.ledger.order_column} DESC LIMIT 100', (owner,)).fetchall()
            return [{**json.loads(row['state']), 'busy': bool(row['busy']), 'execution_mode': row['execution_mode']} for row in rows]

    def delete(self, campaign_id, owner):
        """Permanently remove an owned, idle campaign and its stored records."""
        with self.ledger.connect() as db:
            db.begin_write()
            state, busy = self._read(db, campaign_id, owner)
            mode = db.execute('SELECT execution_mode FROM agent_campaigns WHERE id=?', (campaign_id,)).fetchone()[0]
            if busy or (mode in {'running','step'} and state['status'] in {'ready', 'waiting_jobs', 'awaiting_user'}):
                raise CampaignConflict('Pause the campaign and wait for the current operation to finish before deleting it')
            jobs = db.execute('SELECT status FROM remote_jobs WHERE campaign=?', (campaign_id,)).fetchall()
            if any(row['status'] in ACTIVE for row in jobs):
                raise CampaignConflict('Finish or cancel outstanding calculations before deleting this campaign; uncertain submissions must be resolved first')
            for table in ('remote_jobs', 'campaign_artifacts', 'events', 'actions'):
                db.execute(f'DELETE FROM {table} WHERE campaign=?', (campaign_id,))
            db.execute('DELETE FROM agent_campaigns WHERE id=?', (campaign_id,))
            db.execute('DELETE FROM campaigns WHERE id=?', (campaign_id,))
        return {'id': campaign_id, 'deleted': True}

    def _save(self, state, owner, kind, payload, busy=False):
        with self.ledger.connect() as db:
            db.begin_write()
            self.ledger.owned(db, state['id'], owner)
            current = db.execute('SELECT state,busy,lease_until FROM agent_campaigns WHERE id=?', (state['id'],)).fetchone()
            if (not current['busy'] or current['lease_until'] <= time.time()
                    or json.loads(current['state'])['turns'] != state['turns']):
                raise CampaignConflict('Campaign operation lease was superseded')
            db.execute('UPDATE agent_campaigns SET state=?,busy=?,lease_until=? WHERE id=?',
                       (json.dumps(state, allow_nan=False), int(busy), time.time()+300 if busy else 0, state['id']))
            self.ledger.event(db, state['id'], f"turn-{state['turns']}", kind, payload)

    def message(self, campaign_id, owner, message):
        with self.ledger.connect() as db:
            db.begin_write()
            state, busy = self._read(db, campaign_id, owner)
            if busy or state['status'] != 'awaiting_user':
                raise CampaignConflict('Campaign is not awaiting clarification')
            state['history'].append({'role': 'user', 'content': message})
            state.update(status='ready', question=None)
            db.execute('UPDATE agent_campaigns SET state=?,next_run_at=0 WHERE id=?', (json.dumps(state), campaign_id))
            self.ledger.event(db, campaign_id, 'clarification', 'user_message', {'message': message})
        return self.get(campaign_id, owner)

    def advance(self, campaign_id, owner, model, *, scheduled=False):
        self.get(campaign_id, owner)
        with self.ledger.connect() as db:
            db.begin_write()
            state, busy = self._read(db, campaign_id, owner)
            if scheduled and db.execute('SELECT execution_mode FROM agent_campaigns WHERE id=?', (campaign_id,)).fetchone()[0] not in {'running', 'step'}:
                raise CampaignConflict('Campaign is paused')
            if busy or state['status'] != 'ready':
                raise CampaignConflict('Campaign cannot advance in its current state')
            if state['turns'] >= state['max_turns']:
                state.update(status='budget_exhausted', budget_reason='model_decisions')
                db.execute('UPDATE agent_campaigns SET state=? WHERE id=?', (json.dumps(state), campaign_id))
                self.ledger.event(db, campaign_id, 'campaign', 'budget_exhausted', {})
                return {**state, 'busy': False}
            if state['model'] and state['model'] != model.identity:
                raise CampaignConflict('Campaign model must match its original model')
            if state['agent_spec'] != AGENT_SPEC:
                raise CampaignConflict('Campaign agent instructions have changed')
            for pin in state['skills']:
                if (pin['id'], pin['version']) not in self.allowed:
                    raise CampaignConflict('Campaign skill is no longer authorized')
                try:
                    installed = self.registry.get(pin['id'], pin['version'])
                except ValueError:
                    raise CampaignConflict('Pinned skill version is no longer installed') from None
                if installed.digest != pin['digest']:
                    raise CampaignConflict('Installed skill differs from campaign pin')
            state['model'] = model.identity
            state['turns'] += 1
            db.execute('UPDATE agent_campaigns SET busy=1,state=?,lease_until=? WHERE id=?', (json.dumps(state), time.time()+300, campaign_id))
            self.ledger.event(db, campaign_id, f"turn-{state['turns']}", 'model_started', {'model': model.identity})
        try:
            # Deliberately omit internal lock/pending fields from model context.
            context = {key: compact(state[key]) for key in ('history', 'skills', 'loaded', 'results', 'turns', 'max_turns')}
            context['preferences'] = state.get('preferences', {})
            context['budget'] = self.get(campaign_id, owner)['metrics']
            context['jobs'] = compact(self.jobs.list(campaign_id, owner))
            context['artifacts'] = self.artifacts.list(campaign_id, owner)
            decision = Decision.model_validate(model.decide(context))
            schema = next(t['function']['parameters'] for t in TOOLS if t['function']['name'] == decision.name)
            Draft202012Validator(schema).validate(decision.arguments)
            state['pending'] = decision.model_dump()
            self._save(state, owner, 'model_decision', {'model': model.identity, **decision.model_dump()}, busy=True)
            output = self._act(state, owner, decision)
            state['history'].append({'role': 'assistant', 'decision': decision.model_dump(), 'result': output})
            state['pending'] = None
            self._save(state, owner, 'agent_step_completed', {'status': state['status']})
        except CampaignConflict:
            # An expired operation must never overwrite a recovered campaign.
            return self.get(campaign_id, owner)
        except Exception:
            # If execution was interrupted after claiming an action, never reissue blindly.
            state.update(status='failed', error='agent_step_failed')
            self._save(state, owner, 'agent_step_failed', {'error': 'agent_step_failed'})
        return self.get(campaign_id, owner)

    def _act(self, state, owner, decision):
        args = decision.arguments
        if decision.name == 'ask_user':
            state.update(status='awaiting_user', question=args['question'])
            return {'question': args['question']}
        if decision.name == 'finish':
            if not args['evidence'] or not set(args['evidence']) <= state['results'].keys():
                raise ValueError('Report references unknown evidence')
            if any(state['results'][key].get('kind') in {'remote_job', 'remote_batch'} for key in args['evidence']):
                return {'error': 'remote_job_pending', 'instruction': 'Wait for a recorded job outcome before reporting.'}
            for pin in state['skills']:
                if pin['id'] in state['loaded']:
                    skill = self.registry.get(pin['id'], pin['version'])
                    feedback = self.registry.completion_feedback(skill, state['results'], args['evidence'])
                    if feedback is not None:
                        return feedback
            state.update(status='completed', report=args)
            return args
        if self.authorize:
            try:
                self.authorize(owner, args['skill_id'], state.get('preferences', {}))
            except PermissionError as exc:
                state.update(status='awaiting_user', question=str(exc))
                return {'error': 'skill_access_denied', 'question': str(exc)}
        pin = next((p for p in state['skills'] if p['id'] == args['skill_id']), None)
        if pin is None:
            raise ValueError('Skill is not available in this campaign')
        if decision.name == 'load_skill':
            description = self.registry.describe(pin['id'], pin['version'], self.allowed)
            state['loaded'][pin['id']] = description
            return description
        if pin['id'] not in state['loaded']:
            raise ValueError('Skill must be loaded before execution')
        grants = {(p['id'], p['version'], operation.id) for p in state['skills']
                  for operation in self.registry.get(p['id'], p['version']).manifest.operations}
        action = Action(campaign_id=state['id'], action_id=f"turn-{state['turns']}",
                        skill_id=pin['id'], skill_version=pin['version'], skill_digest=pin['digest'],
                        operation_id=args['operation_id'], inputs=args['inputs'])
        executor = Executor(self.registry, self.ledger, grants={owner: grants}, services=self.services,
                            credentials=self.credentials, jobs=self.jobs, artifacts=self.artifacts)
        try:
            result = executor.execute(action, owner=owner)
        except ExecutionError:
            return {'error': 'invalid_input_or_operation_failed', 'action_id': action.action_id}
        except BudgetExceeded:
            state.update(status='budget_exhausted', budget_reason='tool_actions_or_operation_units')
            return {'error': 'action_budget_exhausted'}
        except LedgerError:
            state.update(status='failed', error='action_requires_review')
            return {'error': 'action_requires_review'}
        # An all-invalid structure batch is retained as tool output, not scientific evidence.
        if 'observations' not in result or result['observations']:
            state['results'][action.action_id] = result
        if result.get('kind') in {'remote_job', 'remote_batch'}:
            state['status'] = 'waiting_jobs'
        return {'action_id': action.action_id, 'output': result}

    def execution(self, campaign_id, owner, mode):
        """Start or pause future work without interrupting an in-flight operation."""
        if mode not in {'running', 'paused', 'step'}:
            raise ValueError('Invalid execution mode')
        with self.ledger.connect() as db:
            db.begin_write()
            state, busy = self._read(db, campaign_id, owner)
            if mode == 'step' and (busy or state['status'] != 'ready'):
                raise CampaignConflict('Campaign cannot advance in its current state')
            if mode in {'running', 'step'} and state['status'] not in {'ready', 'waiting_jobs', 'awaiting_user'}:
                raise CampaignConflict('Resolve the campaign status before resuming')
            db.execute('UPDATE agent_campaigns SET execution_mode=?,execution_error=NULL,next_run_at=0 WHERE id=?', (mode, campaign_id))
            self.ledger.event(db, campaign_id, 'campaign', 'campaign_step_requested' if mode == 'step' else 'campaign_started' if mode == 'running' else 'campaign_paused',
                              {'current_operation_may_finish': busy})
        return self.snapshot(campaign_id, owner)

    def extend_budget(self, campaign_id, owner, max_turns, max_actions, max_units):
        """Apply an owner's explicit approval of new total campaign limits."""
        limits = (max_turns, max_actions, max_units)
        caps = (int(os.getenv('SABLE_MAX_CAMPAIGN_DECISIONS', '1000')),
                int(os.getenv('SABLE_MAX_CAMPAIGN_ACTIONS', '200')),
                int(os.getenv('SABLE_MAX_CAMPAIGN_UNITS', '500')))
        if any(type(value) is not int or value < 1 or value > cap for value, cap in zip(limits, caps)):
            raise CampaignConflict('Requested budget exceeds deployment limits')
        with self.ledger.connect() as db:
            db.begin_write()
            state, busy = self._read(db, campaign_id, owner)
            budget = self.ledger.owned(db, campaign_id, owner)
            before = (state['max_turns'], budget['max_actions'], budget['max_units'])
            if busy or state['status'] != 'budget_exhausted':
                raise CampaignConflict('Campaign is not awaiting a budget increase')
            if any(after < old for after, old in zip(limits, before)) or limits == before:
                raise CampaignConflict('Increase at least one limit without reducing the others')
            state.update(max_turns=max_turns, status='ready', budget_reason=None)
            db.execute('UPDATE campaigns SET max_actions=?,max_units=? WHERE id=?', (max_actions, max_units, campaign_id))
            db.execute("UPDATE agent_campaigns SET state=?,execution_mode='running',execution_error=NULL,next_run_at=0 WHERE id=?", (json.dumps(state), campaign_id))
            self.ledger.event(db, campaign_id, 'campaign', 'budget_increase_approved',
                              {'before': list(before), 'after': list(limits), 'actor_id': str(owner)})
        return self.get(campaign_id, owner)

    def job_operation(self, campaign_id, job_id, owner, operation, remote_id=None):
        state = self.get(campaign_id, owner)
        if state['busy']:
            raise CampaignConflict('Campaign decision is in progress')
        if operation == 'reconcile':
            self.jobs.reconcile(campaign_id, job_id, owner, remote_id)
        else:
            self.jobs.drive(campaign_id, job_id, owner, cancel=operation == 'cancel')
        return self.get(campaign_id, owner)

    def upload_artifact(self, campaign_id, owner, name, content):
        state = self.get(campaign_id, owner)
        if state['busy']:
            raise CampaignConflict('Wait for the current decision before uploading an artifact')
        try:
            self.artifacts.put(campaign_id, owner, name, content, require_idle=True)
        except ValueError as exc:
            raise CampaignConflict(str(exc)) from None
        return self.get(campaign_id, owner)


def compact(value):
    """Keep recorded results intact while bounding bulk data in model context."""
    if isinstance(value, dict):
        return {key: ({'format': 'png', 'available': True} if key == 'figure_png_base64' else {'format': key.removeprefix('figure_'), 'available': True} if key in {'figure_svg', 'figure_html'} else {'retained_row_count': len(item)} if key == 'reaction_rows' and isinstance(item, list)
                      else compact(item)) for key, item in value.items()}
    if isinstance(value, list):
        if len(value) > 16:
            return {'items': [compact(v) for v in value[:16]], 'total_count': len(value), 'truncated': True}
        return [compact(v) for v in value]
    return value
