from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
import pytest

from sable_runtime.agent_model import Decision, OpenAIModel
from sable_runtime.campaigns import CampaignConflict, CampaignService
from sable_runtime.ledger import Ledger
from sable_runtime.registry import SkillRegistry
from server.routers.agent_campaigns import make_router


class ScriptedModel:
    identity = 'test:scripted'
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.contexts = []
    def decide(self, context):
        self.contexts.append(context)
        return next(self.decisions)


def decision(name, **arguments):
    return Decision(name=name, arguments=arguments, usage={'input_tokens': 10, 'output_tokens': 5})


@pytest.fixture
def service(tmp_path):
    registry = SkillRegistry()
    registry.install(Path(__file__).resolve().parents[2] / 'skills/rdkit-descriptors')
    return CampaignService(Ledger(tmp_path / 'agent.sqlite'), registry, {('rdkit-descriptors', '1.0.0')})


def scientific_steps():
    return [decision('load_skill', skill_id='rdkit-descriptors'),
            decision('execute_operation', skill_id='rdkit-descriptors', operation_id='calculate',
                     inputs={'smiles': ['CCO', 'not-a-structure'], 'properties': ['molecular_weight', 'qed']}),
            decision('finish', summary='Calculated descriptors for ethanol; the invalid input was excluded. These do not establish activity.', evidence=['turn-2'])]


def test_natural_language_to_rdkit_and_persistent_evidence(service):
    state = service.create('alice', 'Calculate molecular weight and QED for ethanol SMILES CCO.')
    model = ScriptedModel(scientific_steps())
    state = service.advance(state['id'], 'alice', model)
    # Resume with a fresh service instance using the persisted state and same model identity.
    resumed = CampaignService(Ledger(service.ledger.path), service.registry, service.allowed)
    state = resumed.advance(state['id'], 'alice', model)
    result = state['results']['turn-2']
    observation = result['observations'][0]
    assert observation['canonical_smiles'] == 'CCO'
    assert observation['method_version']
    assert observation['source'] == 'computed'
    assert observation['values'][0]['value'] == pytest.approx(46.069, abs=0.002)
    assert observation['values'][0]['unit'] == 'g/mol'
    assert len(result['failures']) == 1
    state = resumed.advance(state['id'], 'alice', model)
    assert state['status'] == 'completed'
    assert state['report']['evidence'] == ['turn-2']
    events = service.ledger.events(state['id'], 'alice')
    assert [e['sequence'] for e in events] == sorted({e['sequence'] for e in events})
    assert any(e['kind'] == 'model_decision' and e['payload']['usage']['input_tokens'] == 10 for e in events)
    assert len([e for e in events if e['kind'] == 'action_succeeded']) == 1
    with pytest.raises(CampaignConflict):
        resumed.advance(state['id'], 'alice', model)


def test_clarification_resume_and_ownership(service):
    state = service.create('alice', 'What is its QED?')
    model = ScriptedModel([decision('ask_user', question='Please supply the molecule as SMILES.'), *scientific_steps()])
    state = service.advance(state['id'], 'alice', model)
    assert state['status'] == 'awaiting_user'
    with pytest.raises(CampaignConflict):
        service.advance(state['id'], 'alice', model)
    with pytest.raises(PermissionError):
        service.message(state['id'], 'bob', 'CCO')
    state = service.message(state['id'], 'alice', 'CCO')
    assert state['status'] == 'ready'
    assert state['history'][-1]['content'] == 'CCO'
    assert service.list('bob') == []


@pytest.mark.parametrize('choice', [decision('finish', summary='Invented result', evidence=['unknown']),
    decision('execute_operation', skill_id='rdkit-descriptors', operation_id='calculate', inputs={'smiles': ['CCO'], 'properties': ['qed']}),
    decision('load_skill', skill_id='uninstalled')])
def test_invalid_decisions_do_not_create_evidence(service, choice):
    state = service.create('alice', 'Inspect CCO')
    state = service.advance(state['id'], 'alice', ScriptedModel([choice]))
    assert state['status'] == 'failed'
    assert state['results'] == {}
    assert state['report'] is None


def test_model_failure_is_generic_and_consumes_turn(service):
    class Failure:
        identity = 'failure'
        def decide(self, context):
            raise RuntimeError('secret-provider-detail')
    state = service.create('alice', 'Inspect CCO')
    state = service.advance(state['id'], 'alice', Failure())
    assert state['turns'] == 1 and state['status'] == 'failed'
    assert 'secret-provider-detail' not in json.dumps(service.ledger.events(state['id'], 'alice'))


def test_bounded_turns_and_concurrent_advance(service):
    started, release = Event(), Event()
    class Waiting:
        identity = 'waiting'
        def decide(self, context):
            started.set()
            assert release.wait(5)
            return decision('ask_user', question='SMILES?')
    state = service.create('alice', 'Inspect a molecule')
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.advance, state['id'], 'alice', Waiting())
        assert started.wait(5)
        try:
            with pytest.raises(CampaignConflict):
                service.advance(state['id'], 'alice', Waiting())
        finally:
            release.set()
        assert future.result()['status'] == 'awaiting_user'
    state = service.message(state['id'], 'alice', 'CCO')
    with service.ledger.connect() as db:
        state.pop('busy')
        state['turns'] = state['max_turns']
        db.execute('UPDATE agent_campaigns SET state=? WHERE id=?', (json.dumps(state), state['id']))
    assert service.advance(state['id'], 'alice', Waiting())['status'] == 'budget_exhausted'


def test_api_auth_isolation_and_full_campaign(service):
    def user(authorization: str | None = Header(default=None)):
        if authorization not in ('Bearer alice', 'Bearer bob'):
            raise HTTPException(401, 'Authentication required')
        return SimpleNamespace(id=authorization.split()[1])
    model = ScriptedModel(scientific_steps())
    app = FastAPI()
    app.include_router(make_router(user, lambda: service, lambda: model))
    with TestClient(app) as client:
        assert client.get('/v2/campaigns').status_code == 401
        alice = {'Authorization': 'Bearer alice'}
        bob = {'Authorization': 'Bearer bob'}
        response = client.post('/v2/campaigns', headers=alice, json={'message': 'Calculate QED and molecular weight of CCO'})
        assert response.status_code == 200
        path = '/v2/campaigns/' + response.json()['id']
        assert client.get(path, headers=bob).status_code == 404
        assert client.post(path + '/advance', headers=bob).status_code == 404
        assert client.get(path + '/events', headers=bob).status_code == 404
        for _ in range(3):
            response = client.post(path + '/advance', headers=alice)
            assert response.status_code == 200
        assert response.json()['status'] == 'completed'
        assert client.post(path + '/advance', headers=alice).status_code == 409
        events = client.get(path + '/events', headers=alice).json()
        assert client.get(path + '/events', headers=alice, params={'after': events[-1]['sequence']}).json() == []
        assert client.post('/v2/campaigns', headers=alice, json={'message': 'x', 'owner': 'bob'}).status_code == 422


def test_openai_native_tool_contract_and_secret_filter():
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[SimpleNamespace(function=SimpleNamespace(name='ask_user', arguments='{"question":"SMILES?"}'))]))], usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    model = OpenAIModel(api_key='not-a-real-key', model='test-model', client=client)
    assert model.decide({'history': []}).name == 'ask_user'
    assert calls[0]['tool_choice'] == 'required'
    assert calls[0]['parallel_tool_calls'] is False
    assert 'not-a-real-key' not in json.dumps(calls)


def test_revoked_skills_and_changed_model_block_resume(service):
    state = service.create('alice', 'Inspect CCO')
    model = ScriptedModel([decision('load_skill', skill_id='rdkit-descriptors')])
    service.advance(state['id'], 'alice', model)
    model.identity = 'different-model'
    with pytest.raises(CampaignConflict, match='model'):
        service.advance(state['id'], 'alice', model)
    model.identity = 'test:scripted'
    service.allowed = frozenset()
    with pytest.raises(CampaignConflict, match='authorized'):
        service.advance(state['id'], 'alice', model)


def test_invalid_molecules_are_not_report_evidence(service):
    state = service.create('alice', 'Inspect malformed structures')
    model = ScriptedModel([decision('load_skill', skill_id='rdkit-descriptors'),
                           decision('execute_operation', skill_id='rdkit-descriptors', operation_id='calculate',
                                    inputs={'smiles': ['invalid', 'CCO.O'], 'properties': ['qed']})])
    service.advance(state['id'], 'alice', model)
    state = service.advance(state['id'], 'alice', model)
    assert state['results'] == {}
    assert len(state['history'][-1]['result']['output']['failures']) == 2
