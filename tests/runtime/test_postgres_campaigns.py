"""Real PostgreSQL ownership, recovery, and concurrent transaction checks."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4
import os

import pytest

from sable_runtime.campaigns import CampaignConflict, CampaignService
from sable_runtime.contracts import Budget
from sable_runtime.executor import Executor
from sable_runtime.jobs import JobManager
from sable_runtime.ledger import Ledger, LedgerError
from sable_runtime.registry import SkillRegistry
from test_agent import ScriptedModel, decision
from test_jobs import runtime as remote_fixture

pytestmark = pytest.mark.skipif(os.getenv('SABLE_TEST_POSTGRES') != '1', reason='Requires isolated PostgreSQL test database')


@pytest.fixture
def storage():
    ledger = Ledger.configured()
    owners = [str(uuid4()), str(uuid4())]
    with ledger.connect() as db:
        assert db.execute('SELECT current_database()').fetchone()[0] == 'campaign_tests'
        db.execute('TRUNCATE campaigns CASCADE')
        for owner in owners:
            db.execute('''INSERT INTO public.users(id,email,username,auth_provider,is_active,is_verified,created_at,updated_at,extra_metadata)
                VALUES(?,?,?,'local',true,false,now(),now(),'{}')''', (owner, owner+'@example.test', owner))
    yield ledger, *owners
    with ledger.connect() as db:
        db.execute('TRUNCATE campaigns CASCADE')
        for owner in owners:
            db.execute('DELETE FROM public.users WHERE id=?', (owner,))


def service(ledger):
    registry = SkillRegistry()
    registry.install(Path(__file__).resolve().parents[2] / 'skills/dataset-summary')
    return CampaignService(ledger, registry, {('dataset-summary', '1.0.0')})


def test_campaign_roundtrip_and_owner_isolation(storage):
    ledger, alice, bob = storage
    campaigns = service(ledger)
    first = campaigns.create(alice, 'Summarize these observations')
    second = campaigns.create(alice, 'A second campaign')
    assert [row['id'] for row in campaigns.list(alice)] == [second['id'], first['id']]
    assert campaigns.list(bob) == []
    artifact = campaigns.artifacts.put(first['id'], alice, 'data.txt', '1,2,3')
    with pytest.raises(PermissionError):
        campaigns.get(first['id'], bob)
    with pytest.raises(PermissionError):
        campaigns.artifacts.read(first['id'], bob, artifact['id'])
    with pytest.raises(PermissionError):
        ledger.events(first['id'], bob)
    model = ScriptedModel([
        decision('load_skill', skill_id='dataset-summary'),
        decision('execute_operation', skill_id='dataset-summary', operation_id='summarize', inputs={'values': [1, 2, 3], 'unit': 'count'}),
        decision('finish', summary='Mean is 2.', evidence=['turn-2']),
    ])
    for _ in range(3):
        state = service(Ledger(ledger.path)).advance(first['id'], alice, model)
    assert state['status'] == 'completed' and state['metrics']['tool_actions'] == 1
    assert state['results']['turn-2']['mean'] == 2
    assert campaigns.artifacts.list(first['id'], alice)[0]['id'] == artifact['id']
    record = campaigns.export(first['id'], alice)
    assert record['events']
    sequences = [event['sequence'] for event in record['events']]
    assert sequences == sorted(set(sequences))
    assert len(ledger.events(first['id'], alice, after=sequences[0])) == len(sequences)-1
    with ledger.connect() as db:
        db.execute('DELETE FROM public.users WHERE id=?', (alice,))
        assert db.execute('SELECT count(*) FROM campaigns').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM campaign_artifacts').fetchone()[0] == 0


def test_permanent_campaign_deletion(storage):
    ledger, alice, bob = storage
    campaigns = service(ledger)
    campaign = campaigns.create(alice, 'Remove these results')['id']
    other = campaigns.create(bob, 'Keep these results')['id']
    campaigns.artifacts.put(campaign, alice, 'input.txt', '1,2,3')
    model = ScriptedModel([
        decision('load_skill', skill_id='dataset-summary'),
        decision('execute_operation', skill_id='dataset-summary', operation_id='summarize', inputs={'values': [1, 2, 3], 'unit': 'count'}),
    ])
    campaigns.advance(campaign, alice, model)
    campaigns.advance(campaign, alice, model)
    with pytest.raises(PermissionError):
        campaigns.delete(campaign, bob)
    campaigns.delete(campaign, alice)
    assert campaigns.list(alice) == []
    assert campaigns.get(other, bob)['id'] == other
    with ledger.connect() as db:
        for table in ('actions', 'events', 'remote_jobs', 'campaign_artifacts'):
            assert db.execute(f'SELECT count(*) FROM {table} WHERE campaign=?', (campaign,)).fetchone()[0] == 0


def test_concurrent_claims_cannot_overspend(storage):
    ledger, alice, _ = storage
    ledger.create_campaign('budget', alice, Budget(max_actions=1, max_units=1))
    def claim(index):
        try:
            ledger.claim(SimpleNamespace(campaign_id='budget', action_id=str(index)), alice, str(index), 1, {})
            return True
        except LedgerError:
            return False
    with ThreadPoolExecutor(max_workers=8) as workers:
        assert sum(workers.map(claim, range(8))) == 1
    with ledger.connect() as db:
        row = ledger.owned(db, 'budget', alice)
        assert row['used_actions'] == row['used_units'] == 1


def test_other_campaign_not_blocked_by_row_lock(storage):
    ledger, alice, bob = storage
    ledger.create_campaign('first', alice, Budget(max_actions=2, max_units=2))
    ledger.create_campaign('second', bob, Budget(max_actions=2, max_units=2))
    with ThreadPoolExecutor(max_workers=1) as workers:
        with ledger.connect() as db:
            db.begin_write()
            ledger.owned(db, 'first', alice)
            future = workers.submit(ledger.claim, SimpleNamespace(campaign_id='second', action_id='a'), bob, 'f', 1, {})
            assert future.result(timeout=5) is None


def test_agent_lease_blocks_duplicate_model_calls(storage):
    ledger, alice, _ = storage
    campaigns = service(ledger)
    state = campaigns.create(alice, 'Question')
    entered, release = Event(), Event()
    class Model:
        identity = 'test:blocking'
        def decide(self, context):
            entered.set()
            assert release.wait(10)
            return decision('ask_user', question='Which observations?')
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(campaigns.advance, state['id'], alice, Model())
        try:
            assert entered.wait(5)
            with pytest.raises(CampaignConflict):
                service(Ledger(ledger.path)).advance(state['id'], alice, Model())
        finally:
            release.set()
        assert pending.result(timeout=10)['status'] == 'awaiting_user'


def test_remote_jobs_resume_without_duplicate_submission(storage, tmp_path):
    ledger, alice, bob = storage
    fixture = remote_fixture.__wrapped__(tmp_path)
    ledger.create_campaign('campaign', alice, Budget(max_actions=3, max_units=3))
    jobs = JobManager(ledger, fixture.registry, services=fixture.services, clock=lambda: fixture.clock[0])
    executor = Executor(fixture.registry, ledger, grants={alice: {('remote', '1.0.0', 'run')}}, jobs=jobs)
    receipt = executor.execute(fixture.action, owner=alice)
    assert executor.execute(fixture.action, owner=alice) == receipt
    with pytest.raises(PermissionError):
        jobs.drive('campaign', receipt['job_id'], bob)
    assert jobs.drive_due()[0]['status'] == 'running'
    fixture.clock[0] += 2
    resumed = JobManager(Ledger(ledger.path), fixture.registry, services=fixture.services, clock=lambda: fixture.clock[0])
    assert resumed.drive_due()[0]['status'] == 'succeeded'
    assert resumed.list('campaign', alice)[0]['result'] == {'value': 2}
    assert len([e for e in ledger.events('campaign', alice) if e['kind'] == 'job_submit_started']) == 1


def test_foreign_key_and_transaction_rollback(storage):
    ledger, alice, _ = storage
    with pytest.raises(Exception):
        ledger.create_campaign('orphan', str(uuid4()), Budget(max_actions=1, max_units=1))
    ledger.create_campaign('valid', alice, Budget(max_actions=1, max_units=1))
    with pytest.raises(RuntimeError):
        with ledger.connect() as db:
            db.begin_write()
            ledger.owned(db, 'valid', alice)
            db.execute('UPDATE campaigns SET used_actions=1 WHERE id=?', ('valid',))
            ledger.event(db, 'valid', 'a', 'test', {})
            raise RuntimeError('rollback')
    with ledger.connect() as db:
        assert ledger.owned(db, 'valid', alice)['used_actions'] == 0
    assert ledger.events('valid', alice) == []


def test_background_execution_intent_and_concurrent_runners(storage):
    from sable_runtime.runner import CampaignRunner
    ledger, alice, bob = storage
    campaigns = service(ledger)
    state = campaigns.create(alice, 'Summarize observations')
    model = ScriptedModel([decision('load_skill', skill_id='dataset-summary'), decision('ask_user', question='Which values?')])
    runner = CampaignRunner(campaigns, lambda:model)
    runner.tick()
    assert campaigns.get(state['id'], alice)['turns'] == 0
    with pytest.raises(PermissionError):
        campaigns.execution(state['id'], bob, 'running')
    campaigns.execution(state['id'], alice, 'running')
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(lambda _: runner.tick(), range(2)))
    assert len(model.contexts) == 1
    campaigns.execution(state['id'], alice, 'paused')
    runner.tick()
    assert len(model.contexts) == 1
    campaigns.execution(state['id'], alice, 'running')
    runner.tick()
    assert campaigns.get(state['id'], alice)['status'] == 'awaiting_user'


def test_disabled_account_is_not_scheduled(storage):
    from sable_runtime.runner import CampaignRunner
    ledger, alice, _ = storage
    campaigns = service(ledger)
    state = campaigns.create(alice, 'Analyze')
    campaigns.execution(state['id'], alice, 'running')
    with ledger.connect() as db:
        db.execute('UPDATE public.users SET is_active=false WHERE id=?', (alice,))
    CampaignRunner(campaigns, lambda: pytest.fail('Disabled account must not incur model usage')).tick()
    assert campaigns.get(state['id'], alice)['turns'] == 0
