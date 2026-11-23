"""Ownership and execution boundaries for permanent campaign deletion."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace
from test_agent import service
from sable_runtime.campaigns import CampaignConflict
from server.routers.agent_campaigns import make_router


def test_delete_removes_records_and_preserves_other_campaigns(service):
    campaign = service.create('alice', 'Delete me')['id']
    other = service.create('alice', 'Keep me')['id']
    service.artifacts.put(campaign, 'alice', 'input.smi', 'CCO')
    with pytest.raises(PermissionError):
        service.delete(campaign, 'bob')
    assert service.delete(campaign, 'alice')['deleted'] is True
    assert [item['id'] for item in service.list('alice')['items']] == [other]
    with pytest.raises(PermissionError):
        service.get(campaign, 'alice')
    with service.ledger.connect() as db:
        for table in ('remote_jobs', 'campaign_artifacts', 'events', 'actions'):
            assert db.execute(f'SELECT count(*) FROM {table} WHERE campaign=?', (campaign,)).fetchone()[0] == 0


def test_delete_requires_idle_campaign(service):
    campaign = service.create('alice', 'Work')['id']
    service.execution(campaign, 'alice', 'running')
    with pytest.raises(CampaignConflict, match='Pause'):
        service.delete(campaign, 'alice')
    service.execution(campaign, 'alice', 'paused')
    with service.ledger.connect() as db:
        db.execute('UPDATE agent_campaigns SET busy=1 WHERE id=?', (campaign,))
    with pytest.raises(CampaignConflict):
        service.delete(campaign, 'alice')


@pytest.mark.parametrize('status', ['queued', 'submitting', 'running', 'cancelling', 'unknown'])
def test_delete_rejects_outstanding_jobs(service, status):
    campaign = service.create('alice', 'Work')['id']
    with service.ledger.connect() as db:
        db.execute('''INSERT INTO remote_jobs
            (id,campaign,action,skill_id,version,digest,name,payload,binding,status,created,updated,deadline,next_poll)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('job', campaign, 'action', 'skill', '1', 'digest', 'job', '{}', 'binding', status, 0, 0, 0, 0))
    with pytest.raises(CampaignConflict, match='outstanding calculations'):
        service.delete(campaign, 'alice')


def test_delete_endpoint_scopes_owner_and_reports_conflicts(service):
    app = FastAPI()
    app.include_router(make_router(lambda: SimpleNamespace(id='alice'), lambda: service))
    client = TestClient(app)
    other = service.create('bob', 'Private')['id']
    assert client.delete(f'/v2/campaigns/{other}').status_code == 404
    campaign = service.create('alice', 'Work')['id']
    service.execution(campaign, 'alice', 'running')
    assert client.delete(f'/v2/campaigns/{campaign}').status_code == 409
    service.execution(campaign, 'alice', 'paused')
    assert client.delete(f'/v2/campaigns/{campaign}').json()['deleted'] is True
    assert client.delete(f'/v2/campaigns/{campaign}').status_code == 404
