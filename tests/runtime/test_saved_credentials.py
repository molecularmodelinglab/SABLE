"""Saved-credential ownership, durable identity, and SDK transport boundaries."""
import json
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import Mock

import httpx
import pytest
from pydantic import SecretStr

from sable_runtime.jobs import JobManager, JobConflict
from sable_runtime.service_config import load_services
from server.services.campaign_credentials import SavedCredentials
from test_jobs import runtime, enqueue
from test_remote_skills import module, context, screen_payload, screen_row


class Store:
    selected = 'first'
    keys = {'first': 'first-private-key', 'second': 'second-private-key'}
    revoked = False

    def select(self, owner, provider):
        assert owner == 'alice' and provider == 'boltz_platform'
        return self.selected

    def resolve(self, owner, provider, credential_id):
        if owner != 'alice' or self.revoked:
            raise PermissionError('Unavailable')
        return SecretStr(self.keys[credential_id])


def configure(tmp_path, store):
    path = tmp_path / 'sdk.json'
    path.write_text(json.dumps({'example': {'transport': 'sdk', 'credential_provider': 'boltz_platform'}}))
    return load_services(path, credential_store=store)


def test_saved_reference_is_pinned_and_rechecked(tmp_path):
    store = Store(); services, resolve = configure(tmp_path, store)
    assert services['example'].endpoint is None
    pin = resolve.pin('alice', 'example')
    store.selected = 'second'
    assert resolve('alice', pin).get_secret_value() == 'first-private-key'
    assert 'first-private-key' not in pin
    with pytest.raises(PermissionError): resolve('bob', pin)
    store.keys = {**store.keys, 'first': 'replacement-key'}
    with pytest.raises(PermissionError, match='changed'): resolve('alice', pin)


def test_job_resume_uses_pinned_credential_after_selection_changes(runtime, tmp_path, monkeypatch):
    r = runtime; store = Store()
    services, credentials = configure(tmp_path, store)
    r.jobs.services, r.jobs.credentials = services, credentials
    job = enqueue(r)
    store.selected = 'second'
    seen = []
    def handler(skill, spec, phase):
        def submit(payload, ctx):
            seen.append(ctx.secret.get_secret_value())
            return {'remote_id': 'job-a'}
        return submit
    monkeypatch.setattr(r.registry, 'remote_handler', handler)
    resumed = JobManager(r.ledger, r.registry, services=services, credentials=credentials, clock=lambda: r.clock[0])
    assert resumed.drive('campaign', job, 'alice')['status'] == 'running'
    assert seen == ['first-private-key']
    with r.ledger.connect() as db:
        row = dict(db.execute('SELECT * FROM remote_jobs').fetchone())
        assert row['credential_ref'].startswith('saved:boltz_platform:first:')
        assert 'private-key' not in json.dumps(row)
    store.revoked = True
    r.clock[0] += 2
    with pytest.raises(JobConflict, match='credential'): resumed.drive('campaign', job, 'alice', cancel=True)
    assert len(seen) == 1
    assert 'private-key' not in json.dumps(r.ledger.events('campaign', 'alice'))


def test_revoked_key_before_submit_leaves_job_queued_and_locally_cancellable(runtime, tmp_path):
    r = runtime; store = Store()
    r.jobs.services, r.jobs.credentials = configure(tmp_path, store)
    job = enqueue(r); store.revoked = True
    with pytest.raises(JobConflict, match='credential'): r.jobs.drive('campaign', job, 'alice')
    assert r.jobs.list('campaign','alice')[0]['status'] == 'queued'
    assert r.jobs.drive('campaign', job, 'alice', cancel=True)['status'] == 'cancelled'


@pytest.mark.parametrize('status,provider,missing', [('invalid','boltz_platform',False), ('active','other',False), ('active','boltz_platform',True)])
def test_saved_store_requires_owned_active_matching_provider(status, provider, missing):
    owner, credential_id = uuid4(), uuid4()
    session = Mock(); session.__enter__ = Mock(return_value=session); session.__exit__ = Mock(return_value=False)
    service = Mock()
    service.get_owned.return_value = None if missing else SimpleNamespace(status=status, provider=provider, encrypted_secret=b'encrypted')
    store = SavedCredentials(lambda: session, service)
    with pytest.raises(PermissionError): store.resolve(str(owner),'boltz_platform',str(credential_id))
    service.get_owned.assert_called_once_with(session, credential_id, owner)
    service.decrypt.assert_not_called()


def test_sdk_uses_saved_key_hosted_origin_and_bounded_pages(monkeypatch):
    remote = module('boltz-platform'); calls = []
    monkeypatch.setenv('BOLTZ_BASE_URL', 'https://self-hosted.invalid')
    monkeypatch.setenv('BOLTZ_API_KEY', 'deployment-key')
    monkeypatch.setenv('BOLTZ_CUSTOM_HEADERS', 'x-api-key: wrong-key')
    responses = iter([{'id':'job-a'}, {'status':'succeeded'}, {'data':[screen_row()], 'has_more':False}, {'status':'stopped'}])
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=next(responses))
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda self, request: respond(request))
    assert remote.submit(screen_payload(), context()) == {'remote_id':'job-a'}
    assert remote.poll('job-a', screen_payload(), context())['status'] == 'succeeded'
    assert remote.cancel('job-a', {}, context())['status'] == 'cancelled'
    assert [request.method for request in calls] == ['POST','GET','GET','POST']
    assert all(request.url.host == 'api.boltz.bio' for request in calls)
    assert all(request.headers['x-api-key'] == 'private-fixture-token' for request in calls)
    assert json.loads(calls[0].content)['target']['entities'][0]['value'] == 'MKTV'
    assert calls[2].url.params['limit'] == '100'


def test_sdk_rejects_oversized_response_without_retry(monkeypatch):
    remote = module('boltz-platform'); calls=[]
    def respond(self, request):
        calls.append(request)
        return httpx.Response(200, content=b'x' * 4_000_001)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', respond)
    with pytest.raises(RuntimeError): remote._call(context(),'retrieve','job-a')
    assert len(calls) == 1


def test_account_selection_and_owned_credential_query(monkeypatch):
    from cryptography.fernet import Fernet
    User = pytest.importorskip("server.models.user").User
    CredentialService = pytest.importorskip("server.services.credential_service").CredentialService
    from server.models.provider_credential import ProviderCredential
    from unittest.mock import MagicMock
    owner, credential_id = uuid4(), uuid4()
    user = SimpleNamespace(extra_metadata={'boltz_settings': {'provider':'platform','credential_id':str(credential_id)}})
    monkeypatch.setenv('PROVIDER_CREDENTIAL_MASTER_KEY', Fernet.generate_key().decode())
    service = CredentialService()
    credential = SimpleNamespace(status='active',provider='boltz_platform',encrypted_secret=service.encrypt('account-key'))
    session = MagicMock(); session.__enter__.return_value = session
    user_query, credential_query = Mock(), Mock()
    user_query.filter.return_value.first.return_value = user
    credential_query.filter.return_value.first.return_value = credential
    session.query.side_effect = lambda model: user_query if model is User else credential_query
    store = SavedCredentials(lambda:session,service)
    assert store.select(str(owner),'boltz_platform') == str(credential_id)
    assert store.resolve(str(owner),'boltz_platform',str(credential_id)).get_secret_value() == 'account-key'
    user_filter = user_query.filter.call_args.args[0]
    assert list(user_filter.compile().params.values()) == [owner]
    filters = credential_query.filter.call_args.args
    assert [list(term.compile().params.values())[0] for term in filters] == [credential_id,owner]
    session.query.assert_any_call(ProviderCredential)
    user.extra_metadata['boltz_settings']['provider'] = 'self_hosted'
    with pytest.raises(PermissionError): store.select(str(owner),'boltz_platform')
    credential.status='invalid'
    with pytest.raises(PermissionError): store.resolve(str(owner),'boltz_platform',str(credential_id))
    credential_query.filter.return_value.first.return_value = None
    with pytest.raises(PermissionError): store.resolve(str(owner),'boltz_platform',str(credential_id))


@pytest.mark.parametrize('entry', [
    {'transport':'sdk','endpoint':'https://override.invalid'},
    {'transport':'sdk','credential_env':'KEY','credential_provider':'boltz_platform'},
    {'transport':'other'},
    {'transport':'sdk','credential_provider':'invalid:provider'},
])
def test_sdk_binding_rejects_ambiguous_sources(tmp_path, entry):
    path=tmp_path/'bindings.json';path.write_text(json.dumps({'example':entry}))
    with pytest.raises(ValueError): load_services(path,credential_store=Store())


def test_reconciliation_checks_pinned_key_after_account_selection_changes(runtime, tmp_path):
    r=runtime; store=Store()
    r.jobs.services,r.jobs.credentials=configure(tmp_path,store)
    job=enqueue(r)
    with r.ledger.connect() as db:
        db.execute("UPDATE remote_jobs SET status='unknown' WHERE id=?",(job,))
    store.selected='second'
    with pytest.raises(JobConflict,match='Credential material'):
        r.jobs.reconcile('campaign',job,'alice','first-private-key')
    store.revoked=True
    with pytest.raises(JobConflict,match='credential'):
        r.jobs.reconcile('campaign',job,'alice','job-a')


def test_sdk_does_not_read_error_bodies(monkeypatch):
    remote=module('boltz-platform'); calls=[]; reads=[]
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            reads.append(True)
            raise AssertionError('Error bodies must not be consumed')
            yield b''
    def respond(self,request):
        calls.append(request)
        return httpx.Response(503,stream=Body())
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',respond)
    with pytest.raises(RuntimeError):remote._call(context(),'retrieve','job-a')
    assert len(calls)==1 and reads == []
