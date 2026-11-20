"""Batch scoring, target identity and cumulative optimization across providers."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from pydantic import SecretStr
from sable_runtime.campaigns import CampaignService
from sable_runtime.contracts import Action
from sable_runtime.executor import Executor, ServiceBinding
from sable_runtime.jobs import JobConflict
from sable_runtime.ledger import Ledger
from sable_runtime.registry import SkillRegistry
from sable_runtime.science import measurements, validate_targets
from sable_runtime.job_results import resolved_job_results
from test_remote_skills import module

ROOT = Path(__file__).resolve().parents[2] / 'skills'


@pytest.fixture(params=['boltz', 'boltz-platform'])
def binding(request, tmp_path, monkeypatch):
    backend = request.param
    registry = SkillRegistry()
    for name in (backend, 'baybe'): registry.install(ROOT / name)
    allowed = set(registry._skills)
    service = CampaignService(Ledger(tmp_path / 'campaign.sqlite'), registry, allowed,
        services={backend: ServiceBinding('https://provider.invalid', 'test')},
        credentials=lambda *args: SecretStr('test-key'))
    campaign = service.create('alice', 'Optimize two batches of three')['id']
    candidates = [{'id': f'm{i}', 'smiles': smiles} for i, smiles in enumerate(['CCO','CCCO','CCCCO','CCN','CCCN','CCCCN','CCOC'])]
    library = {'kind':'candidate_library','candidates':candidates,'constraints':{'scaffold_smarts':None,'property_filters':[]},'starting_smiles':'CO'}
    action = SimpleNamespace(campaign_id=campaign, action_id='library')
    service.ledger.claim(action, 'alice', 'library', 1, {})
    service.ledger.finish(action, 'alice', 'succeeded', library)
    grants = {'alice': {(s.manifest.id,s.manifest.version,op.id) for s in registry._skills.values() for op in s.manifest.operations}}
    executor = Executor(registry, service.ledger, grants=grants, jobs=service.jobs)
    calls = []
    remote = module(backend)
    original = registry.remote_handler
    if backend == 'boltz':
        def provider(context, method, path, **kwargs):
            calls.append((method,path))
            return {'job_id': 'job-'+str(len(calls))} if method == 'POST' else {'status':'completed','affinity':-2.0}
        monkeypatch.setattr(remote, '_request', provider)
    else:
        payloads = {}
        def provider(context, op, remote_id=None, **kwargs):
            calls.append((op,remote_id))
            if op == 'start':
                identifier='job-'+str(len(calls)); payloads[identifier]=kwargs['molecules']; return {'id':identifier}
            if op == 'retrieve': return {'status':'succeeded'}
            return {'data':[{'id':'result-'+row['id'],'external_id':row['id'],'smiles':row['smiles'],
                            'metrics':{'optimization_score':0.6}} for row in payloads[remote_id]],'has_more':False}
        monkeypatch.setattr(remote, '_call', provider)
    monkeypatch.setattr(registry, 'remote_handler', lambda skill,spec,phase: getattr(remote, getattr(spec,phase)) if skill.manifest.id==backend else original(skill,spec,phase))
    def compute(skill,name,payload):
        measured={row['Molecule_ID'] for row in payload['measurements']}
        return {'recommended_ids':[key for key in payload['search_space'] if key not in measured][:payload['batch_size']]}
    monkeypatch.setattr('sable_runtime.executor.run_worker',compute)
    clock=[1000.0];service.jobs.clock=lambda:clock[0]
    def execute(name, operation, action_id, inputs):
        skill=next(s for s in registry._skills.values() if s.manifest.id==name)
        return executor.execute(Action(campaign_id=campaign,action_id=action_id,skill_id=name,
             skill_version=skill.manifest.version,skill_digest=skill.digest,operation_id=operation,inputs=inputs),owner='alice')
    return SimpleNamespace(backend=backend, service=service, campaign=campaign, execute=execute, calls=calls, clock=clock)


def test_two_rounds_three_candidates_and_final_ranking(binding):
    r=binding
    target={'name':'binding_affinity','mode':'MIN'} if r.backend=='boltz' else {'name':'boltz_optimization_score','mode':'MAX'}
    refs=[]
    for round in (1,2):
        recommendation=r.execute('baybe','recommend',f'recommend-{round}',{'library_action_id':'library','measurement_action_ids':refs,
            'targets':[target],'strategy':'single','batch_size':3,'max_rounds':2})
        assert len(recommendation['candidates'])==3
        receipt=r.execute(r.backend,'screen',f'screen-{round}',{'source_action_id':f'recommend-{round}','proteins':[{'chain_id':'A','sequence':'MACK'}]})
        assert receipt['kind']==('remote_batch' if r.backend=='boltz' else 'remote_job')
        jobs=[j for j in r.service.jobs.list(r.campaign,'alice') if j['action']==f'screen-{round}']
        assert len(jobs)==(3 if r.backend=='boltz' else 1)
        # Replaying the queued operation cannot create extra jobs or submissions.
        assert r.execute(r.backend,'screen',f'screen-{round}',{'source_action_id':f'recommend-{round}','proteins':[{'chain_id':'A','sequence':'MACK'}]})==receipt
        for job in jobs:r.service.jobs.drive(r.campaign,job['id'],'alice')
        assert f'screen-{round}' not in resolved_job_results(r.service.jobs.list(r.campaign,'alice'))
        r.clock[0]+=20
        for job in jobs:r.service.jobs.drive(r.campaign,job['id'],'alice')
        refs.append(f'screen-{round}')
    report=r.execute('baybe','rank','ranking',{'library_action_id':'library','measurement_action_ids':refs,'targets':[target],'strategy':'single'})
    assert report['evaluated_candidates']==6
    assert len(report['rankings'])==6
    submissions=[call for call in r.calls if call[0] in {'POST','start'}]
    assert len(submissions)==(6 if r.backend=='boltz' else 2)


def test_batch_uncertainty_never_resubmits_siblings(binding, monkeypatch):
    r=binding
    r.execute(r.backend,'screen','screen',{'source_action_id':'library','proteins':[{'chain_id':'A','sequence':'MACK'}]})
    jobs=r.service.jobs.list(r.campaign,'alice')
    original=r.service.registry.remote_handler
    def crash(*args):raise TimeoutError('potentially accepted')
    monkeypatch.setattr(r.service.registry,'remote_handler',lambda *args:crash)
    assert r.service.jobs.drive(r.campaign,jobs[0]['id'],'alice')['status']=='unknown'
    monkeypatch.setattr(r.service.registry,'remote_handler',original)
    with pytest.raises(JobConflict):r.service.jobs.drive(r.campaign,jobs[0]['id'],'alice')
    for job in jobs[1:]:r.service.jobs.drive(r.campaign,job['id'],'alice')
    assert 'screen' not in resolved_job_results(r.service.jobs.list(r.campaign,'alice'))


def test_targets_and_provenance_cannot_be_mixed():
    library={'kind':'candidate_library','candidates':[{'id':'a','smiles':'CCO'}]}
    result={'kind':'boltz_prediction','provider':'self_hosted','library_action_id':'library','protein_scope_id':'boltz-target:one',
            'measurement':{'id':'a','smiles':'CCO','metrics':{'affinity':-2}}}
    other=deepcopy(result);other['protein_scope_id']='boltz-target:two'
    target=[{'name':'binding_affinity','mode':'MIN'}]
    with pytest.raises(ValueError,match='protein targets'):
        measurements({'library':library,'one':result,'two':other},'library',['one','two'],target)
    with pytest.raises(ValueError,match='direction'):
        validate_targets([{'name':'boltz_optimization_score','mode':'MIN'}],'single')
    other=deepcopy(result);other['measurement']['smiles']='CCN'
    with pytest.raises(ValueError,match='identity'):
        measurements({'library':library,'one':other},'library',['one'],target)


def test_completed_binding_scores_require_ranked_evidence():
    checker = module('baybe','completion')
    result={'kind':'boltz_prediction','provider':'self_hosted','library_action_id':'library','protein_scope_id':'boltz-target:test',
            'measurement':{'id':'a','smiles':'CCO','metrics':{'affinity':-2}}}
    results={'recommend':{'kind':'recommendation','library_action_id':'library'},
             'scores':{'kind':'remote_batch_result','results':[result],'failures':[]}}
    assert checker.check(results,['scores'])['error']=='ranking_required'
    results['ranking']={'kind':'optimization_report','library_action_id':'library','measurement_action_ids':['scores']}
    assert checker.check(results,['ranking']) is None


def test_failed_batch_members_remain_visible_without_fabricated_scores():
    jobs=[{'id':'one','action':'batch','status':'succeeded','result':{'value':1}},
          {'id':'two','action':'batch','status':'failed','error':'provider_failure'}]
    result=resolved_job_results(jobs)['batch']
    assert result['results']==[{'value':1}]
    assert result['failures'][0]['job_id']=='two'
    jobs[1]['status']='unknown'
    assert resolved_job_results(jobs)=={}


@pytest.mark.parametrize('name,mode', [('binding_affinity','MIN'),('boltz_optimization_score','MAX')])
def test_real_baybe_worker_accepts_native_score_objectives(name, mode):
    from sable_runtime.local_worker import run_worker
    registry=SkillRegistry();registry.install(ROOT/'baybe')
    skill=next(iter(registry._skills.values()))
    smiles=['CCO','CCCO','CCCCO','CCN','CCCN','CCCCN','CCOC']
    output=run_worker(skill,'compute',{'targets':[{'name':name,'mode':mode}],'strategy':'single',
        'batch_size':3,'search_space':{f'm{i}':s for i,s in enumerate(smiles)},
        'measurements':[{'Molecule_ID':f'm{i}',name:float(i)} for i in range(3)],'seed':42})
    assert len(set(output['recommended_ids']))==3
    assert not set(output['recommended_ids']) & {'m0','m1','m2'}
