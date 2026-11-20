"""Campaign intent, background progress, budget approval, and pause boundaries."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import json
import pytest
from sable_runtime.campaigns import CampaignConflict, CampaignService
from sable_runtime.runner import CampaignRunner
from test_agent import service, ScriptedModel, decision, scientific_steps
from test_jobs import runtime as remote_fixture


def test_draft_requires_start_and_browser_is_not_required(service):
    state = service.create('alice', 'Calculate properties')
    clock = [1000.0]
    model = ScriptedModel(scientific_steps())
    runner = CampaignRunner(service, lambda: model, clock=lambda: clock[0])
    runner.tick()
    assert service.get(state['id'], 'alice')['turns'] == 0
    service.execution(state['id'], 'alice', 'running')
    for _ in range(3):
        runner.tick(); clock[0] += 6
    result = service.get(state['id'], 'alice')
    assert result['status'] == 'completed'
    runner.tick()
    assert len(model.contexts) == 3
    assert model.contexts[0]['budget']['max_tool_actions'] == 20


def test_clarification_then_background_resume(service):
    state = service.create('alice', 'Analyze')
    model = ScriptedModel([decision('ask_user', question='Which structure?'), decision('load_skill', skill_id='rdkit-descriptors')])
    runner = CampaignRunner(service, lambda: model)
    service.execution(state['id'], 'alice', 'running')
    runner.tick()
    runner.tick()
    assert len(model.contexts) == 1
    service.message(state['id'], 'alice', 'CCO')
    runner.tick()
    assert len(model.contexts) == 2


def test_pause_during_model_call_survives_step_save(service):
    state = service.create('alice', 'Analyze')
    service.execution(state['id'], 'alice', 'running')
    entered, release = Event(), Event()
    class Model:
        identity = 'test:pause'
        def decide(self, context):
            entered.set(); assert release.wait(10)
            return decision('load_skill', skill_id='rdkit-descriptors')
    runner = CampaignRunner(service, Model)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(runner.tick)
        try:
            assert entered.wait(5)
            service.execution(state['id'], 'alice', 'paused')
        finally:
            release.set()
        future.result(timeout=10)
    assert service.get(state['id'], 'alice')['execution_mode'] == 'paused'
    assert service.get(state['id'], 'alice')['turns'] == 1
    runner.tick()
    assert service.get(state['id'], 'alice')['turns'] == 1
    with pytest.raises(PermissionError):
        service.execution(state['id'], 'bob', 'running')


def test_budget_requires_explicit_owner_approval(service):
    state = service.create('alice', 'Analyze')
    with service.ledger.connect() as db:
        state['max_turns'] = 1
        db.execute('UPDATE agent_campaigns SET state=? WHERE id=?', (json.dumps(state), state['id']))
    service.execution(state['id'], 'alice', 'running')
    model = ScriptedModel([decision('load_skill', skill_id='rdkit-descriptors')])
    clock = [1000.0]
    runner = CampaignRunner(service, lambda:model, clock=lambda:clock[0])
    runner.tick(); clock[0]+=6; runner.tick()
    assert service.get(state['id'], 'alice')['status'] == 'budget_exhausted'
    runner.tick()
    assert len(model.contexts) == 1
    with pytest.raises(PermissionError):
        service.extend_budget(state['id'], 'bob', 2, 20, 50)
    with pytest.raises(CampaignConflict):
        service.extend_budget(state['id'], 'alice', 100000, 20, 50)
    updated = service.extend_budget(state['id'], 'alice', 2, 20, 50)
    assert updated['status'] == 'ready' and updated['execution_mode'] == 'running'
    assert any(event['kind'] == 'budget_increase_approved' for event in service.ledger.events(state['id'], 'alice'))


def test_remote_job_completion_automatically_returns_to_model(tmp_path):
    remote = remote_fixture.__wrapped__(tmp_path)
    campaign_service = CampaignService(remote.ledger, remote.registry, {('remote','1.0.0')}, services=remote.services)
    clock = [1000.0]
    campaign_service.jobs.clock = lambda:clock[0]
    state = campaign_service.create('alice', 'Run calculation')
    model = ScriptedModel([
        decision('load_skill', skill_id='remote'),
        decision('execute_operation', skill_id='remote', operation_id='run', inputs={'value':2}),
        decision('finish', summary='Calculation completed', evidence=['turn-2'])])
    runner = CampaignRunner(campaign_service, lambda:model, clock=lambda:clock[0])
    campaign_service.execution(state['id'], 'alice', 'running')
    for _ in range(6):
        runner.tick(); clock[0]+=6
    result = campaign_service.get(state['id'], 'alice')
    assert result['status']=='completed' and result['results']['turn-2']=={'value':2}
    assert len(model.contexts)==3


def test_configuration_failure_pauses_instead_of_retries(service):
    state=service.create('alice','Analyze')
    service.execution(state['id'],'alice','running')
    calls=[]
    def fail():
        calls.append(1)
        raise ValueError('provider secret must not be exposed')
    runner=CampaignRunner(service,fail)
    runner.tick();runner.tick()
    result=service.get(state['id'],'alice')
    assert len(calls)==1 and result['execution_mode']=='paused'
    assert 'secret' not in result['execution_error']


def test_upload_requires_a_pause_or_clarification(service):
    state=service.create('alice','Analyze')
    service.upload_artifact(state['id'],'alice','input.txt','CCO')
    service.execution(state['id'],'alice','running')
    with pytest.raises(CampaignConflict,match='Pause'):
        service.upload_artifact(state['id'],'alice','next.txt','CCN')
    service.execution(state['id'],'alice','paused')
    assert len(service.upload_artifact(state['id'],'alice','next.txt','CCN')['artifacts'])==2


def test_account_provider_preference_reaches_model(service):
    service.preferences = lambda owner: {'boltz_provider':'platform','boltz_skill_id':'boltz-platform'}
    state = service.create('alice', 'Optimize binding')
    model = ScriptedModel([decision('ask_user', question='Which target?')])
    service.advance(state['id'], 'alice', model)
    assert model.contexts[0]['preferences']['boltz_skill_id'] == 'boltz-platform'
