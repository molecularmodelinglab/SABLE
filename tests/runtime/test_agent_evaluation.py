"""Campaign evaluation outcomes, recovery, and execution budgets."""
from copy import deepcopy
import json

from sable_runtime.campaigns import CampaignService
from sable_runtime.ledger import Ledger
from sable_runtime.registry import SkillRegistry
from campaign_evaluation import ROOT, ReplayModel, assess, cases, evaluate, solvent_inputs
from test_agent import ScriptedModel, decision


def test_scripted_outcome_suite_and_no_false_release_approval():
    report = evaluate(ReplayModel, mode='scripted-runtime')
    assert report['summary']['passed'] == report['summary']['total'] == 8
    assert report['summary']['operation_error_rate'] == 0
    assert report['summary']['unsupported_claims'] is None
    assert report['release']['approved'] is False
    assert report['skills'] and report['case_sha256'] and report['agent_spec']
    # Missing evidence, fabricated rankings and unsupported completion cannot pass.
    result = next(row for row in report['cases'] if row['id'] == 'solvent-screen')
    state = deepcopy(result['state'])
    state['results'][state['report']['evidence'][0]]['ranking'][0]['relative_energy_difference'] = 999
    assert not assess(next(case for case in cases() if case['id'] == 'solvent-screen'), state)


def test_budget_exhaustion_has_no_extra_model_calls(tmp_path):
    registry = SkillRegistry(); registry.install(ROOT / 'skills/solvent-screening')
    service = CampaignService(Ledger(tmp_path / 'budget.sqlite'), registry, registry.available())
    state = service.create('alice', 'Screen solvents')
    with service.ledger.connect() as db:
        raw = json.loads(db.execute('SELECT state FROM agent_campaigns WHERE id=?', (state['id'],)).fetchone()[0])
        raw['max_turns'] = 1
        db.execute('UPDATE agent_campaigns SET state=? WHERE id=?', (json.dumps(raw),state['id']))
    model = ScriptedModel([decision('load_skill', skill_id='solvent-screening')])
    service.advance(state['id'], 'alice', model)
    assert service.advance(state['id'], 'alice', model)['status'] == 'budget_exhausted'
    assert len(model.contexts) == 1


def test_evaluation_captures_error_category_without_provider_details():
    class BrokenModel:
        identity = 'test:unavailable'
        def decide(self, context):
            raise TimeoutError('private-provider-detail')
    report = evaluate(lambda case: BrokenModel(), mode='scripted-runtime', selected=cases()[:1])
    assert report['cases'][0]['model_errors'] == ['TimeoutError']
    assert report['summary']['passed'] == 0
    assert 'private-provider-detail' not in json.dumps(report)


def test_failed_input_can_recover_without_fabricated_evidence(tmp_path):
    registry = SkillRegistry(); registry.install(ROOT / 'skills/solvent-screening')
    service = CampaignService(Ledger(tmp_path / 'recovery.sqlite'), registry, registry.available())
    state = service.create('alice', 'Screen supplied solvents')
    model = ScriptedModel([decision('load_skill', skill_id='solvent-screening'),
        decision('execute_operation', skill_id='solvent-screening', operation_id='screen', inputs={}),
        decision('execute_operation', skill_id='solvent-screening', operation_id='screen', inputs=solvent_inputs()),
        decision('finish', summary='Computed screening estimates, not measured solubility.', evidence=['turn-3'])])
    for _ in range(4): state = service.advance(state['id'], 'alice', model)
    assert state['status'] == 'completed'
    assert set(state['results']) == {'turn-3'}
    assert state['history'][2]['result']['error'] == 'invalid_input_or_operation_failed'
