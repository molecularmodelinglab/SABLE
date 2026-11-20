"""Contributor acceptance and immutable package lifecycle checks."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys

import pytest
from sable_runtime.skill_templates import create_package
from sable_runtime.skill_validation import validate_package
from sable_runtime.skill_store import SkillStore, load_deployment_skills, deployment_revision
from sable_runtime.campaigns import CampaignService, CampaignConflict
from sable_runtime.ledger import Ledger
from test_agent import ScriptedModel, decision


def cli(*arguments, success=True):
    result = subprocess.run([sys.executable,'-m','sable_runtime.skills',*map(str,arguments)],capture_output=True,text=True)
    assert result.returncode == (0 if success else 1), result.stdout+result.stderr
    return json.loads(result.stdout)


def test_contributor_adds_capability_without_orchestrator_changes(tmp_path):
    package=tmp_path/'text-statistics';store=tmp_path/'installed'
    cli('init','text-statistics','--output',package,'--template','worker')
    # The contributor edits only their package, changing its computation.
    (package/'worker.py').write_text('def count(inputs):\n    return {"characters": len(inputs["text"].replace(" ", ""))}\n')
    assert cli('validate',package)['valid']
    cli('install',package,'--store',store,'--activate')
    registry,allowed,selected=SkillStore(store).load()
    service=CampaignService(Ledger(tmp_path/'campaign.sqlite'),registry,allowed,discovery_allowed=selected)
    state=service.create('alice','Count non-space characters in A B')
    model=ScriptedModel([decision('load_skill',skill_id='text-statistics'),
        decision('execute_operation',skill_id='text-statistics',operation_id='count',inputs={'text':'A B'}),
        decision('finish',summary='Two non-space characters.',evidence=['turn-2'])])
    for _ in range(3):state=service.advance(state['id'],'alice',model)
    assert state['status']=='completed' and state['results']['turn-2']=={'characters':2}
    assert state['skills'][0]['digest']==cli('validate',package)['digest']


def test_versions_upgrade_disable_and_revoke_preserve_pins(tmp_path):
    package=create_package('example',tmp_path/'source')
    store=SkillStore(tmp_path/'store');store.install(package,activate=True)
    registry,allowed,selected=store.load()
    ledger=Ledger(tmp_path/'campaign.sqlite')
    service=CampaignService(ledger,registry,allowed,discovery_allowed=selected)
    old=service.create('alice','Count text')
    manifest=json.loads((package/'manifest.json').read_text());manifest['version']='1.1.0'
    (package/'manifest.json').write_text(json.dumps(manifest))
    store.install(package,activate=True)
    registry,allowed,selected=store.load()
    upgraded=CampaignService(ledger,registry,allowed,discovery_allowed=selected)
    assert upgraded.create('alice','New campaign')['skills'][0]['version']=='1.1.0'
    assert upgraded.advance(old['id'],'alice',ScriptedModel([decision('load_skill',skill_id='example')]))['status']=='ready'
    store.set_state('example','1.1.0','disable')
    assert store.load()[2]==set()
    store.set_state('example','1.0.0','revoke')
    registry,allowed,selected=store.load()
    revoked=CampaignService(ledger,registry,allowed,discovery_allowed=selected)
    with pytest.raises(CampaignConflict,match='authorized'):
        revoked.advance(old['id'],'alice',ScriptedModel([]))
    with pytest.raises(ValueError,match='permanent'):store.set_state('example','1.0.0','activate')


def test_validation_does_not_execute_package_and_checks_entrypoints(tmp_path):
    package=create_package('example',tmp_path/'source');marker=tmp_path/'executed'
    code=(package/'operations.py').read_text()
    (package/'operations.py').write_text(f'open({str(marker)!r}, "w").write("executed")\n'+code)
    validate_package(package)
    assert not marker.exists()
    SkillStore(tmp_path/'store').install(package)
    assert not marker.exists()
    (package/'operations.py').write_text('def other(inputs, context):\n return {}\n')
    with pytest.raises(ValueError,match='top-level'):validate_package(package)


def test_immutable_versions_detect_source_and_store_tampering(tmp_path):
    package=create_package('example',tmp_path/'source');store=SkillStore(tmp_path/'store')
    store.install(package)
    (package/'operations.py').write_text('def count(inputs, context):\n return {"characters": 9}\n')
    with pytest.raises(ValueError,match='immutable'):store.install(package)
    target=store.root/'packages/example/1.0.0/operations.py';target.write_text('def count(inputs, context):\n return {}\n')
    with pytest.raises(ValueError,match='digest'):store.load()


def test_templates_and_static_validation_errors(tmp_path):
    for template in ('inline','worker','workflow'):
        package=create_package(template,tmp_path/template,template)
        assert validate_package(package).manifest.id==template
        with pytest.raises(ValueError,match='exists'):create_package(template,package,template)
    remote=create_package('remote-example',tmp_path/'remote','remote')
    assert 'unfinished' in cli('validate',remote,success=False)['error']
    package=tmp_path/'inline';(package/'SKILL.md').write_text('missing metadata')
    with pytest.raises(ValueError,match='frontmatter'):validate_package(package)


def test_all_bundled_packages_validate_without_provider_access():
    for manifest in (Path(__file__).resolve().parents[2]/'skills').glob('*/manifest.json'):
        assert validate_package(manifest.parent).digest


def test_store_locking_selection_and_revision(tmp_path,monkeypatch):
    first=create_package('example',tmp_path/'a');second=create_package('example',tmp_path/'b')
    data=json.loads((second/'manifest.json').read_text());data['version']='1.1.0';(second/'manifest.json').write_text(json.dumps(data))
    store=SkillStore(tmp_path/'store')
    monkeypatch.setenv('SABLE_SKILL_STORE_PATH',str(store.root))
    before=deployment_revision()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda path:store.install(path,activate=True),(first,second)))
    assert sum(r['active'] for r in store.records())==1
    assert deployment_revision()!=before
    assert len(load_deployment_skills(tmp_path/'unused')[2])==1


def test_symlink_package_storage_is_rejected(tmp_path):
    package=create_package('example',tmp_path/'source');store=SkillStore(tmp_path/'store')
    store.root.mkdir();outside=tmp_path/'outside';outside.mkdir()
    (store.root/'packages').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='symlinks'):store.install(package)
    assert not list(outside.iterdir())


def test_cli_smoke_and_schema(tmp_path):
    package=create_package('example',tmp_path/'source','worker')
    inputs=tmp_path/'inputs.json';inputs.write_text('{"text":"ABC"}')
    assert cli('smoke',package,'--operation','count','--inputs',inputs)['result']=={'characters':3}
    assert '$defs' in cli('schema')


def test_catalog_pagination_exports_and_artifact_access(tmp_path):
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from server.routers.agent_campaigns import make_router
    package=create_package('example',tmp_path/'source')
    store=SkillStore(tmp_path/'store');store.install(package,activate=True)
    registry,allowed,selected=store.load()
    service=CampaignService(Ledger(tmp_path/'campaign.sqlite'),registry,allowed,discovery_allowed=selected)
    def user(authorization: str|None=Header(default=None)):
        if not authorization:raise HTTPException(401)
        return SimpleNamespace(id=authorization)
    app=FastAPI();app.include_router(make_router(user,lambda:service))
    state=service.create('alice','Example');cid=state['id']
    artifact=service.artifacts.put(cid,'alice','protein.pdb','ATOM coordinate content')
    with service.ledger.connect() as db:
        for index in range(5):service.ledger.event(db,cid,'action','example',{'index':index,'content':'ATOM coordinate content','long':'x'*3000})
    base='/v2/campaigns'
    with TestClient(app) as client:
        assert client.get(base+'/capabilities').status_code==401
        assert client.get(base+'/capabilities',headers={'authorization':'alice'}).json()[0]['available']
        first=client.get(f'{base}/{cid}/events?limit=2&preview=true',headers={'authorization':'alice'}).json()
        second=client.get(f'{base}/{cid}/events?after={first[-1]["sequence"]}&limit=2&preview=true',headers={'authorization':'alice'}).json()
        assert not {e['event_id'] for e in first}&{e['event_id'] for e in second}
        event=second[-1]
        assert len(event['payload']['long'])<3000
        full=client.get(f'{base}/{cid}/events/{event["sequence"]}',headers={'authorization':'alice'}).json()
        assert len(full['payload']['long'])==3000
        exported=client.get(f'{base}/{cid}/export',headers={'authorization':'alice'}).json()
        assert len(exported['events'])==7 and exported['schema_version']==1
        assert 'ATOM coordinate content' not in json.dumps(exported)
        assert exported['events'][-1]['payload']['content']=={'artifact_id':artifact['id']}
        path=f'{base}/{cid}/artifacts/{artifact["id"]}'
        assert client.get(path,headers={'authorization':'alice'}).json()['content']=='ATOM coordinate content'
        for suffix in ('/events','/export',f'/events/{event["sequence"]}',f'/artifacts/{artifact["id"]}'):
            assert client.get(f'{base}/{cid}'+suffix,headers={'authorization':'bob'}).status_code==404


def test_running_api_refreshes_activation_and_revocation(tmp_path,monkeypatch):
    from server.routers.agent_campaigns import configured_service, _configured_service
    monkeypatch.setattr('server.routers.agent_campaigns.campaign_preferences', lambda owner: {})
    package=create_package('example',tmp_path/'source');store=SkillStore(tmp_path/'store');store.install(package,activate=True)
    monkeypatch.setenv('SABLE_AGENT_ENABLED','1')
    monkeypatch.setattr(Ledger, 'configured', classmethod(lambda cls: cls(tmp_path/'campaign.sqlite')))
    monkeypatch.setenv('SABLE_SKILL_STORE_PATH',str(store.root))
    monkeypatch.delenv('SABLE_SERVICE_BINDINGS_PATH',raising=False)
    _configured_service.cache_clear()
    try:
        first=configured_service();old=first.create('alice','Before update')
        data=json.loads((package/'manifest.json').read_text());data['version']='1.1.0';(package/'manifest.json').write_text(json.dumps(data))
        store.install(package,activate=True)
        second=configured_service()
        assert second is not first
        assert second.create('alice','After update')['skills'][0]['version']=='1.1.0'
        assert ('example','1.0.0') in second.allowed
        store.set_state('example','1.0.0','revoke')
        third=configured_service()
        with pytest.raises(CampaignConflict,match='authorized'):
            third.advance(old['id'],'alice',ScriptedModel([]))
    finally:
        _configured_service.cache_clear()
