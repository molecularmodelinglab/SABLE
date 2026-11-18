"""Local durable, bounded agent campaigns. One model decision per advance."""
import json
from uuid import uuid4

from jsonschema import Draft202012Validator
from .agent_model import AGENT_SPEC, Decision, TOOLS
from .contracts import Action, Budget
from .executor import Executor, ExecutionError
from .ledger import LedgerError


class CampaignConflict(RuntimeError):
    pass


class CampaignService:
    def __init__(self, ledger, registry, allowed):
        self.ledger, self.registry = ledger, registry
        self.allowed = frozenset(allowed)  # Host-configured skill/version pairs.
        with ledger.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS agent_campaigns (
                id TEXT PRIMARY KEY, state TEXT NOT NULL, busy INTEGER NOT NULL DEFAULT 0)''')

    def create(self, owner, prompt):
        campaign_id = 'campaign-' + uuid4().hex
        pins = self.registry.discover(self.allowed)
        state = dict(id=campaign_id, status='ready', history=[{'role': 'user', 'content': prompt}],
                     skills=pins, loaded={}, results={}, report=None, question=None,
                     turns=0, max_turns=24, model=None, pending=None, error=None, agent_spec=AGENT_SPEC)
        with self.ledger.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO campaigns(id,owner,max_actions,max_units) VALUES(?,?,?,?)',
                       (campaign_id, owner, 16, 20))
            db.execute('INSERT INTO agent_campaigns(id,state) VALUES(?,?)', (campaign_id, json.dumps(state)))
            self.ledger.event(db, campaign_id, 'campaign', 'campaign_created', {'prompt': prompt, 'skills': pins})
        return self.get(campaign_id, owner)

    def _read(self, db, campaign_id, owner):
        self.ledger.owned(db, campaign_id, owner)
        row = db.execute('SELECT * FROM agent_campaigns WHERE id=?', (campaign_id,)).fetchone()
        if row is None:
            raise PermissionError('Campaign is not available')
        return json.loads(row['state']), bool(row['busy'])

    def get(self, campaign_id, owner):
        with self.ledger.connect() as db:
            state, busy = self._read(db, campaign_id, owner)
            return {**state, 'busy': busy}

    def list(self, owner):
        with self.ledger.connect() as db:
            rows = db.execute('SELECT a.state,a.busy FROM agent_campaigns a JOIN campaigns c ON a.id=c.id WHERE c.owner=? ORDER BY a.rowid DESC LIMIT 100', (owner,)).fetchall()
            return [{**json.loads(row['state']), 'busy': bool(row['busy'])} for row in rows]

    def _save(self, state, owner, kind, payload, busy=False):
        with self.ledger.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.ledger.owned(db, state['id'], owner)
            db.execute('UPDATE agent_campaigns SET state=?,busy=? WHERE id=?',
                       (json.dumps(state, allow_nan=False), int(busy), state['id']))
            self.ledger.event(db, state['id'], f"turn-{state['turns']}", kind, payload)

    def message(self, campaign_id, owner, message):
        with self.ledger.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state, busy = self._read(db, campaign_id, owner)
            if busy or state['status'] != 'awaiting_user':
                raise CampaignConflict('Campaign is not awaiting clarification')
            state['history'].append({'role': 'user', 'content': message})
            state.update(status='ready', question=None)
            db.execute('UPDATE agent_campaigns SET state=? WHERE id=?', (json.dumps(state), campaign_id))
            self.ledger.event(db, campaign_id, 'clarification', 'user_message', {'message': message})
        return self.get(campaign_id, owner)

    def advance(self, campaign_id, owner, model):
        with self.ledger.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state, busy = self._read(db, campaign_id, owner)
            if busy or state['status'] != 'ready':
                raise CampaignConflict('Campaign cannot advance in its current state')
            if state['turns'] >= state['max_turns']:
                state['status'] = 'budget_exhausted'
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
            db.execute('UPDATE agent_campaigns SET busy=1,state=? WHERE id=?', (json.dumps(state), campaign_id))
            self.ledger.event(db, campaign_id, f"turn-{state['turns']}", 'model_started', {'model': model.identity})
        try:
            # Deliberately omit internal lock/pending fields from model context.
            context = {key: compact(state[key]) for key in ('history', 'skills', 'loaded', 'results', 'turns', 'max_turns')}
            decision = Decision.model_validate(model.decide(context))
            schema = next(t['function']['parameters'] for t in TOOLS if t['function']['name'] == decision.name)
            Draft202012Validator(schema).validate(decision.arguments)
            state['pending'] = decision.model_dump()
            self._save(state, owner, 'model_decision', {'model': model.identity, **decision.model_dump()}, busy=True)
            output = self._act(state, owner, decision)
            state['history'].append({'role': 'assistant', 'decision': decision.model_dump(), 'result': output})
            state['pending'] = None
            self._save(state, owner, 'agent_step_completed', {'status': state['status']})
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
            for pin in state['skills']:
                if pin['id'] in state['loaded']:
                    skill = self.registry.get(pin['id'], pin['version'])
                    feedback = self.registry.completion_feedback(skill, state['results'], args['evidence'])
                    if feedback is not None:
                        return feedback
            state.update(status='completed', report=args)
            return args
        pin = next(p for p in state['skills'] if p['id'] == args['skill_id'])
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
        executor = Executor(self.registry, self.ledger, grants={owner: grants})
        try:
            result = executor.execute(action, owner=owner)
        except ExecutionError:
            return {'error': 'invalid_input_or_operation_failed', 'action_id': action.action_id}
        except LedgerError:
            state['status'] = 'budget_exhausted'
            return {'error': 'action_budget_exhausted'}
        # An all-invalid structure batch is retained as tool output, not scientific evidence.
        if 'observations' not in result or result['observations']:
            state['results'][action.action_id] = result
        return {'action_id': action.action_id, 'output': result}


def compact(value):
    """Keep recorded results intact while bounding bulk data in model context."""
    if isinstance(value, dict):
        return {key: ({'retained_row_count': len(item)} if key == 'reaction_rows' and isinstance(item, list)
                      else compact(item)) for key, item in value.items()}
    if isinstance(value, list):
        if len(value) > 16:
            return {'items': [compact(v) for v in value[:16]], 'total_count': len(value), 'truncated': True}
        return [compact(v) for v in value]
    return value
