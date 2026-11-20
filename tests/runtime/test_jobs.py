"""Durable job recovery, ownership, cancellation, and submission boundaries."""
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import json

import pytest
from pydantic import SecretStr

from sable_runtime.contracts import Action, Budget
from sable_runtime.executor import Executor, ServiceBinding
from sable_runtime.jobs import JobManager, JobConflict
from sable_runtime.ledger import Ledger
from sable_runtime.registry import SkillRegistry


@pytest.fixture
def runtime(tmp_path):
    folder = tmp_path / 'remote'; folder.mkdir()
    output = {'type': 'object', 'additionalProperties': False, 'properties': {'value': {'type': 'number'}}, 'required': ['value']}
    receipt = {'type': 'object', 'additionalProperties': False, 'properties': {
        'kind': {'const': 'remote_job'}, 'job_id': {'type': 'string'}, 'status': {'type': 'string'}},
        'required': ['kind', 'job_id', 'status']}
    manifest = {'id': 'remote', 'version': '1.0.0', 'description': 'Remote example',
                'requirements': {'services': ['example']},
                'operations': [{'id': 'run', 'handler': 'run', 'description': 'Submit work',
                                'input_schema': output, 'output_schema': receipt}],
                'remote_jobs': {'compute': {'file': 'remote.py', 'submit': 'submit', 'poll': 'poll', 'cancel': 'cancel',
                                           'service': 'example', 'timeout_seconds': 60, 'poll_interval_seconds': 1,
                                           'output_schema': output}}}
    (folder / 'manifest.json').write_text(json.dumps(manifest))
    (folder / 'SKILL.md').write_text('Remote computation')
    (folder / 'operations.py').write_text("def run(inputs, context):\n return context.submit_job('compute', inputs)\n")
    (folder / 'remote.py').write_text("def submit(payload, context):\n return {'remote_id':'provider-job'}\ndef poll(remote_id,payload,context):\n return {'status':'succeeded','result':payload}\ndef cancel(remote_id,payload,context):\n return {'status':'cancelled'}\n")
    registry = SkillRegistry(); registry.install(folder)
    ledger = Ledger(tmp_path / 'jobs.sqlite'); ledger.create_campaign('campaign', 'alice', Budget(max_actions=3, max_units=3))
    clock = [1000.0]
    services = {'example': ServiceBinding('https://example.invalid')}
    jobs = JobManager(ledger, registry, services=services, clock=lambda: clock[0])
    executor = Executor(registry, ledger, grants={'alice': {('remote','1.0.0','run')}}, jobs=jobs)
    action = Action(campaign_id='campaign', action_id='one', skill_id='remote', skill_version='1.0.0',
                    skill_digest=registry.get('remote','1.0.0').digest, operation_id='run', inputs={'value': 2})
    return SimpleNamespace(folder=folder, registry=registry, ledger=ledger, jobs=jobs, executor=executor,
                           action=action, clock=clock, services=services)


def enqueue(r):
    return r.executor.execute(r.action, owner='alice')['job_id']


def test_enqueue_restart_and_observe_without_recharging(runtime):
    r = runtime; job = enqueue(r)
    assert r.jobs.list('campaign','alice')[0]['status'] == 'queued'
    assert enqueue(r) == job
    assert r.jobs.drive('campaign',job,'alice')['status'] == 'running'
    resumed = JobManager(Ledger(r.ledger.path), r.registry, services=r.services, clock=lambda:r.clock[0])
    r.clock[0] += 2
    assert resumed.drive('campaign',job,'alice')['result'] == {'value':2}
    with r.ledger.connect() as db:
        assert db.execute('SELECT used_actions FROM campaigns').fetchone()[0] == 1
    assert resumed.drive('campaign',job,'alice')['status'] == 'succeeded'


def test_uncertain_submission_never_retried_and_explicit_reconciliation(runtime, monkeypatch):
    r=runtime; job=enqueue(r); calls=[]
    original=r.registry.remote_handler
    def fail(*args):
        calls.append(1)
        raise TimeoutError('potentially accepted')
    monkeypatch.setattr(r.registry,'remote_handler',lambda *args:fail)
    assert r.jobs.drive('campaign',job,'alice')['status']=='unknown'
    with pytest.raises(JobConflict,match='reconciliation'):
        r.jobs.drive('campaign',job,'alice')
    assert len(calls)==1
    monkeypatch.setattr(r.registry,'remote_handler',original)
    r.jobs.reconcile('campaign',job,'alice','confirmed-provider-job')
    assert r.jobs.drive('campaign',job,'alice')['status']=='succeeded'


def test_crashed_submission_becomes_unknown(runtime):
    r=runtime; job=enqueue(r)
    with r.ledger.connect() as db:
        db.execute("UPDATE remote_jobs SET status='submitting',lease_until=999,token='old'")
    assert r.jobs.drive('campaign',job,'alice')['status']=='unknown'
    with pytest.raises(JobConflict): r.jobs.drive('campaign',job,'alice')


def test_cancellation_and_timeout_do_not_claim_remote_termination(runtime):
    r=runtime; job=enqueue(r)
    r.jobs.drive('campaign',job,'alice')
    r.clock[0]+=61
    assert r.jobs.drive('campaign',job,'alice')['status']=='timed_out'
    # An explicit later observation can recover results from a still-running server job.
    assert r.jobs.drive('campaign',job,'alice')['status']=='succeeded'
    second=r.action.model_copy(update={'action_id':'two'})
    job2=r.executor.execute(second,owner='alice')['job_id']
    assert r.jobs.drive('campaign',job2,'alice',cancel=True)['status']=='cancelled'
    third=r.action.model_copy(update={'action_id':'three'})
    job3=r.executor.execute(third,owner='alice')['job_id']
    r.jobs.drive('campaign',job3,'alice')
    assert r.jobs.drive('campaign',job3,'alice',cancel=True)['status']=='cancelled'


def test_owner_and_binding_isolation(runtime):
    r=runtime; job=enqueue(r)
    for call in (lambda:r.jobs.list('campaign','bob'),lambda:r.jobs.drive('campaign',job,'bob'),
                 lambda:r.jobs.reconcile('campaign',job,'bob','other')):
        with pytest.raises(PermissionError):call()
    r.jobs.services['example']=ServiceBinding('https://other.invalid')
    with pytest.raises(JobConflict,match='endpoint'):r.jobs.drive('campaign',job,'alice')


def test_partial_results_and_invalid_results(runtime, monkeypatch):
    r=runtime; job=enqueue(r)
    r.jobs.drive('campaign',job,'alice');r.clock[0]+=2
    monkeypatch.setattr(r.registry,'remote_handler',lambda *args:lambda *args:{'status':'partial','result':{'value':1}})
    assert r.jobs.drive('campaign',job,'alice')['status']=='partial'
    second=r.action.model_copy(update={'action_id':'two'})
    job2=r.executor.execute(second,owner='alice')['job_id']
    monkeypatch.setattr(r.registry,'remote_handler',lambda *args:lambda *args:{'status':'succeeded','result':{'invented':2}})
    assert r.jobs.drive('campaign',job2,'alice')['status']=='unknown'
    assert r.jobs.list('campaign','alice')[1]['result'] is None


def test_concurrent_submissions_and_late_response_fencing(runtime, monkeypatch):
    r=runtime; job=enqueue(r); entered=Event();release=Event()
    def submit(*args):
        entered.set();release.wait(10)
        return {'remote_id':'accepted'}
    monkeypatch.setattr(r.registry,'remote_handler',lambda *args:submit)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result=pool.submit(r.jobs.drive,'campaign',job,'alice')
        assert entered.wait(5)
        with pytest.raises(JobConflict,match='progress'):r.jobs.drive('campaign',job,'alice')
        r.clock[0]+=301
        assert r.jobs.drive('campaign',job,'alice')['status']=='unknown'
        release.set()
        with pytest.raises(JobConflict,match='superseded'):result.result()
    assert r.jobs.list('campaign','alice')[0]['status']=='unknown'


def test_credential_outputs_never_persist(runtime, monkeypatch):
    r=runtime; r.jobs.services['example']=ServiceBinding('https://example.invalid','example-key')
    r.jobs.credentials=lambda owner,ref:SecretStr('private-test-token')
    job=enqueue(r)
    monkeypatch.setattr(r.registry,'remote_handler',lambda *args:lambda *args:{'remote_id':'private-test-token'})
    assert r.jobs.drive('campaign',job,'alice')['status']=='unknown'
    assert 'private-test-token' not in json.dumps(r.ledger.events('campaign','alice'))


def test_background_progress_and_pin_changes(runtime):
    r=runtime;job=enqueue(r)
    assert r.jobs.drive_due()==[{'job_id':job,'status':'running'}]
    r.clock[0]+=2
    assert r.jobs.drive_due()==[{'job_id':job,'status':'succeeded'}]
    assert r.jobs.drive_due()==[]
    second=r.action.model_copy(update={'action_id':'two'})
    job2=r.executor.execute(second,owner='alice')['job_id']
    (r.folder/'remote.py').write_text('def submit(payload,context):\n return {}\n')
    replacement=SkillRegistry();replacement.install(r.folder);r.jobs.registry=replacement
    with pytest.raises(JobConflict,match='changed'):r.jobs.drive('campaign',job2,'alice')


def test_campaign_wait_resume_and_restart_recovery(runtime):
    from sable_runtime.campaigns import CampaignService
    from test_agent import ScriptedModel, decision
    r=runtime
    service=CampaignService(r.ledger,r.registry,{('remote','1.0.0')},services=r.services)
    state=service.create('alice','Compute a value remotely')
    model=ScriptedModel([decision('load_skill',skill_id='remote'),
                         decision('execute_operation',skill_id='remote',operation_id='run',inputs={'value':2}),
                         decision('finish',summary='Computed value 2',evidence=['turn-2'])])
    service.advance(state['id'],'alice',model)
    state=service.advance(state['id'],'alice',model)
    assert state['status']=='waiting_jobs' and state['turns']==2
    job=state['jobs'][0]['id']
    service.job_operation(state['id'],job,'alice','poll')
    resumed=CampaignService(Ledger(r.ledger.path),r.registry,{('remote','1.0.0')},services=r.services)
    with resumed.ledger.connect() as db:db.execute('UPDATE remote_jobs SET next_poll=0 WHERE id=?',(job,))
    state=resumed.job_operation(state['id'],job,'alice','poll')
    assert state['status']=='ready' and state['turns']==2
    assert state['results']['turn-2']=={'value':2}
    assert resumed.advance(state['id'],'alice',model)['status']=='completed'


def test_committed_enqueue_survives_interrupted_campaign_save(runtime):
    from sable_runtime.campaigns import CampaignService
    from test_agent import ScriptedModel, decision
    r=runtime;service=CampaignService(r.ledger,r.registry,{('remote','1.0.0')},services=r.services)
    state=service.create('alice','Compute')
    service.advance(state['id'],'alice',ScriptedModel([decision('load_skill',skill_id='remote')]))
    state=service.get(state['id'],'alice');state['turns']=2
    pending=decision('execute_operation',skill_id='remote',operation_id='run',inputs={'value':2})
    state['pending']=pending.model_dump()
    service._act(state,'alice',pending)
    # Simulate the process dying after the ledger commit but before saving campaign output.
    state['results']={};state['status']='ready'
    with r.ledger.connect() as db:
        db.execute('UPDATE agent_campaigns SET state=?,busy=1,lease_until=0 WHERE id=?',(json.dumps(state),state['id']))
    recovered=service.get(state['id'],'alice')
    assert recovered['status']=='waiting_jobs'
    assert recovered['results']['turn-2']['kind']=='remote_job'
    assert len(recovered['jobs'])==1


def test_job_endpoints_require_owner_and_operator_reconciliation(runtime):
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.testclient import TestClient
    from sable_runtime.campaigns import CampaignService
    from server.routers.agent_campaigns import make_router
    from test_agent import ScriptedModel, decision
    r=runtime;service=CampaignService(r.ledger,r.registry,{('remote','1.0.0')},services=r.services)
    state=service.create('alice','Compute')
    model=ScriptedModel([decision('load_skill',skill_id='remote'),decision('execute_operation',skill_id='remote',operation_id='run',inputs={'value':2})])
    service.advance(state['id'],'alice',model);state=service.advance(state['id'],'alice',model)
    job=state['jobs'][0]['id']
    def user(authorization: str|None=Header(default=None)):
        if not authorization:raise HTTPException(401)
        return SimpleNamespace(id=authorization,has_role=lambda role:authorization=='admin')
    app=FastAPI();app.include_router(make_router(user,lambda:service))
    path=f"/v2/campaigns/{state['id']}/jobs/{job}"
    with TestClient(app) as client:
        assert client.post(path+'/poll',headers={'authorization':'bob'}).status_code==404
        assert client.post(path+'/cancel',headers={'authorization':'bob'}).status_code==404
        assert client.post(path+'/reconcile',headers={'authorization':'alice'},json={'remote_id':'x'}).status_code==403
        with r.ledger.connect() as db:db.execute("UPDATE remote_jobs SET status='unknown' WHERE id=?",(job,))
        assert client.post(path+'/reconcile',headers={'authorization':'admin'},json={'remote_id':'verified'}).status_code==200


def test_service_configuration_uses_credential_references(tmp_path, monkeypatch):
    from sable_runtime.service_config import load_services
    config=tmp_path/'bindings.json'
    config.write_text(json.dumps({'example':{'endpoint':'https://example.invalid/api','credential_env':'EXAMPLE_TOKEN'}}))
    monkeypatch.setenv('EXAMPLE_TOKEN','private-key')
    services,resolver=load_services(str(config))
    assert services['example'].endpoint=='https://example.invalid/api'
    assert resolver('alice',services['example'].credential).get_secret_value()=='private-key'
    assert 'private-key' not in repr(services)
    config.write_text(json.dumps({'example':{'endpoint':'https://user:secret@example.invalid'}}))
    with pytest.raises(ValueError):load_services(str(config))


def test_failed_remote_work_can_be_reported_without_inventing_observations(runtime, monkeypatch):
    from sable_runtime.campaigns import CampaignService
    from test_agent import ScriptedModel, decision
    r=runtime;service=CampaignService(r.ledger,r.registry,{('remote','1.0.0')},services=r.services)
    state=service.create('alice','Compute')
    model=ScriptedModel([decision('load_skill',skill_id='remote'),decision('execute_operation',skill_id='remote',operation_id='run',inputs={'value':2}),decision('finish',summary='Remote computation failed; no measurement was obtained.',evidence=['turn-2'])])
    service.advance(state['id'],'alice',model);state=service.advance(state['id'],'alice',model)
    monkeypatch.setattr(r.registry,'remote_handler',lambda *args:lambda *args:{'status':'failed'})
    job=state['jobs'][0]['id'];state=service.job_operation(state['id'],job,'alice','poll')
    assert state['results']['turn-2']['kind']=='job_outcome'
    assert service.advance(state['id'],'alice',model)['status']=='completed'


def test_pending_cancellation_remains_visible_until_confirmed(runtime, monkeypatch):
    r=runtime;job=enqueue(r);r.jobs.drive('campaign',job,'alice')
    monkeypatch.setattr(r.registry,'remote_handler',lambda *args:lambda *args:{'status':'running'})
    assert r.jobs.drive('campaign',job,'alice',cancel=True)['status']=='cancelling'
    r.clock[0]+=2
    assert r.jobs.drive('campaign',job,'alice')['status']=='cancelling'
    monkeypatch.setattr(r.registry,'remote_handler',lambda *args:lambda *args:{'status':'cancelled'})
    r.clock[0]+=2
    assert r.jobs.drive('campaign',job,'alice')['status']=='cancelled'


def test_artifact_ownership_metadata_and_model_context(runtime):
    from sable_runtime.campaigns import CampaignService
    from test_agent import ScriptedModel, decision
    r=runtime;service=CampaignService(r.ledger,r.registry,{('remote','1.0.0')},services=r.services)
    state=service.create('alice','Use a coordinate file')
    state=service.upload_artifact(state['id'],'alice','protein.pdb','ATOM coordinate data')
    artifact=state['artifacts'][0]
    assert 'content' not in artifact
    assert service.artifacts.read(state['id'],'alice',artifact['id'])=='ATOM coordinate data'
    other=service.create('alice','Other campaign')
    with pytest.raises(PermissionError):service.artifacts.read(other['id'],'alice',artifact['id'])
    with pytest.raises(PermissionError):service.artifacts.read(state['id'],'bob',artifact['id'])
    model=ScriptedModel([decision('ask_user',question='Which calculation?')])
    service.advance(state['id'],'alice',model)
    assert model.contexts[0]['artifacts']==[artifact]
    assert 'ATOM coordinate data' not in json.dumps(model.contexts[0])
    with pytest.raises(ValueError):service.artifacts.put(state['id'],'alice','huge.txt','x'*3_000_001)


def test_unsubmitted_timeout_and_cancel_never_contact_provider(runtime, monkeypatch):
    r=runtime;job=enqueue(r);r.clock[0]+=61
    assert r.jobs.drive('campaign',job,'alice')['status']=='timed_out'
    monkeypatch.setattr(r.registry,'remote_handler',lambda *args:pytest.fail('Unsubmitted job contacted provider'))
    assert r.jobs.drive('campaign',job,'alice')['status']=='timed_out'
    r.jobs.services.clear()
    assert r.jobs.drive('campaign',job,'alice',cancel=True)['status']=='cancelled'


def test_queued_cancel_survives_revoked_configuration(runtime):
    r=runtime;job=enqueue(r);r.jobs.services.clear();r.jobs.allowed=set()
    assert r.jobs.drive('campaign',job,'alice',cancel=True)['status']=='cancelled'


def test_late_result_invalidates_a_report_that_cited_timeout(runtime):
    from sable_runtime.campaigns import CampaignService
    from test_agent import ScriptedModel, decision
    r=runtime;service=CampaignService(r.ledger,r.registry,{('remote','1.0.0')},services=r.services)
    state=service.create('alice','Compute')
    model=ScriptedModel([decision('load_skill',skill_id='remote'),decision('execute_operation',skill_id='remote',operation_id='run',inputs={'value':2}),decision('finish',summary='Remote waiting timed out, outcome not yet known.',evidence=['turn-2'])])
    service.advance(state['id'],'alice',model);state=service.advance(state['id'],'alice',model)
    job=state['jobs'][0]['id'];service.job_operation(state['id'],job,'alice','poll')
    with r.ledger.connect() as db:db.execute('UPDATE remote_jobs SET deadline=0,next_poll=0 WHERE id=?',(job,))
    service.job_operation(state['id'],job,'alice','poll')
    assert service.advance(state['id'],'alice',model)['status']=='completed'
    state=service.job_operation(state['id'],job,'alice','poll')
    assert state['status']=='ready' and state['report'] is None
    assert state['results']['turn-2']=={'value':2}
    assert any(e['kind']=='report_invalidated' for e in r.ledger.events(state['id'],'alice'))


def test_batch_enqueue_rolls_back_every_item_on_failure(runtime):
    r=runtime
    r.jobs.services['example']=ServiceBinding('https://example.invalid','test-key')
    r.jobs.credentials=lambda *args:SecretStr('secret-value')
    skill=r.registry.get('remote','1.0.0')
    with pytest.raises(JobConflict,match='Credential'):
        r.jobs.enqueue_many(r.action,skill,'compute',[{'value':1},{'value':'secret-value'}],'alice')
    assert r.jobs.list('campaign','alice')==[]
