"""HEALER API package contract and normalization with mocked HTTP only."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from jsonschema import Draft202012Validator
from pydantic import SecretStr
import pytest
import requests

from sable_runtime.science import canonical, filter_library
from sable_runtime.contracts import Action, Budget, Manifest
from sable_runtime.executor import Executor, ServiceBinding
from sable_runtime.jobs import JobManager
from sable_runtime.ledger import Ledger
from sable_runtime.registry import SkillRegistry


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / 'skills' / 'healer-api'
MANIFEST = json.loads((PACKAGE / 'manifest.json').read_text())


def module(name):
    spec = importlib.util.spec_from_file_location('healer_api_' + name, PACKAGE / f'{name}.py')
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture
def api(monkeypatch):
    operations, remote = module('operations'), module('remote')
    http = Mock(side_effect=AssertionError('Unexpected HTTP request'))
    monkeypatch.setattr(remote.requests, 'request', http)
    monkeypatch.setenv('HEALER_STOCK', 'test-stock')
    context = SimpleNamespace(service=SimpleNamespace(endpoint='https://healer.example/api/'),
                              secret=None, results={}, submit_job=Mock(return_value={
                                  'kind': 'remote_job', 'job_id': 'durable-1', 'status': 'pending'}))
    return operations, remote, http, context


def response(data):
    result = Mock(status_code=200)
    result.iter_content.return_value = [json.dumps(data).encode()]
    return result


def payload(api, **inputs):
    operations, _, _, context = api
    receipt = operations.enumerate({'starting_smiles': 'OCC', 'max_candidates': 16, **inputs}, context)
    Draft202012Validator(MANIFEST['operations'][0]['output_schema']).validate(receipt)
    assert receipt == {'kind': 'remote_job', 'job_id': 'durable-1', 'status': 'pending'}
    assert context.submit_job.call_args.args[0] == 'enumeration'
    return context.submit_job.call_args.args[1]


def test_manifest_library_contract_and_dependencies():
    Manifest.model_validate(MANIFEST)
    local = json.loads((ROOT / 'skills/healer/manifest.json').read_text())
    job = MANIFEST['remote_jobs']['enumeration']
    assert job == {'file': 'remote.py', 'submit': 'submit', 'poll': 'poll',
                   'service': 'healer-api', 'timeout_seconds': 900, 'poll_interval_seconds': 5,
                   'output_schema': local['operations'][0]['output_schema']}
    assert MANIFEST['requirements']['services'] == ['healer-api']
    assert set(MANIFEST['requirements']['modules']) == {'rdkit', 'requests'}
    assert not MANIFEST.get('workers')
    for schema in [job['output_schema'], MANIFEST['operations'][0]['input_schema'],
                   MANIFEST['operations'][0]['output_schema']]:
        Draft202012Validator.check_schema(schema)
        assert schema['additionalProperties'] is False


def test_package_installs_remote_adapter_snapshot():
    registry = SkillRegistry()
    registry.install(PACKAGE)
    installed = registry.get('healer-api', '1.0.0')
    assert installed.files['remote.py'] == (PACKAGE / 'remote.py').read_bytes()
    assert callable(registry.handler(installed, installed.manifest.operations[0]))


def test_durable_enqueue_then_drive_uses_service_credential(api, tmp_path):
    _, _, http, _ = api
    registry = SkillRegistry()
    registry.install(PACKAGE)
    skill = registry.get('healer-api', '1.0.0')
    assert registry.available() == set()
    assert registry.available(services=['healer-api']) == {('healer-api', '1.0.0')}
    ledger = Ledger(tmp_path / 'ledger.sqlite')
    ledger.create_campaign('campaign', 'alice', Budget(max_actions=2, max_units=4))
    now = [100.0]
    services = {'healer-api': ServiceBinding('https://healer.example', credential='healer-token')}
    credentials = Mock(spec=lambda owner, reference: None, return_value=SecretStr('test-service-secret'))
    jobs = JobManager(ledger, registry, services=services, credentials=credentials, clock=lambda: now[0])
    executor = Executor(registry, ledger, grants={'alice': {('healer-api', '1.0.0', 'enumerate')}},
                        services=services, jobs=jobs)
    action = Action(campaign_id='campaign', action_id='library', skill_id='healer-api',
                    skill_version='1.0.0', skill_digest=skill.digest, operation_id='enumerate',
                    inputs={'starting_smiles': 'CCO', 'max_candidates': 4})
    receipt = executor.execute(action, owner='alice')
    assert receipt['status'] == 'queued'
    http.assert_not_called()
    http.side_effect = [response({'status': 'submitted', 'job_id': 'actual-job'}),
                        response({'status': 'SUCCESS', 'result': {'complete': [{'Product': 'CCN'}]}})]
    assert jobs.drive('campaign', receipt['job_id'], 'alice')['status'] == 'running'
    now[0] += 5
    completed = jobs.drive('campaign', receipt['job_id'], 'alice')
    assert completed['status'] == 'succeeded'
    assert completed['action'] == 'library'
    assert completed['result']['candidates'][0]['smiles'] == 'CCN'
    assert completed['result']['provenance']['remote_job']['remote_id'] == 'actual-job'
    assert http.call_count == 2
    assert all(call.kwargs['headers'] == {'Authorization': 'Bearer test-service-secret'}
               for call in http.call_args_list)
    credentials.assert_called_with('alice', 'healer-token')
    with ledger.connect() as db:
        stored_payload = db.execute('SELECT payload FROM remote_jobs').fetchone()['payload']
    assert 'test-service-secret' not in stored_payload


def test_submit_contract_and_seed_not_sent(api):
    _, remote, http, context = api
    inputs = payload(api, seed=123, reaction_tags=['amide'])
    http.side_effect = None
    http.return_value = response({'status': 'submitted', 'job_id': 'remote-1'})
    assert remote.submit(inputs, context) == {'remote_id': 'remote-1'}
    http.assert_called_once_with('POST', 'https://healer.example/api/enumerate/molecule',
                                 headers={}, timeout=(5, 30), allow_redirects=False, stream=True,
                                 json={'molecule': 'OCC', 'bb_source': 'test-stock',
                                       'reaction_tags': ['amide'], 'max_bbs_per_frag': 5,
                                       'n_compositions': 4, 'max_total_products': 16})


def test_site_atom_order_and_optional_auth(api):
    _, remote, http, context = api
    inputs = payload(api, mode='SiteHEALER', reactive_sites=[0])
    context.secret = SecretStr('test-bearer')
    http.side_effect = None
    http.return_value = response({'status': 'submitted', 'job_id': 'site-1'})
    remote.submit(inputs, context)
    args, kwargs = http.call_args
    assert args == ('POST', 'https://healer.example/api/enumerate/site')
    assert kwargs['json']['molecule'] == 'OCC'
    assert kwargs['json']['reactive_sites'] == [0]
    assert kwargs['headers'] == {'Authorization': 'Bearer test-bearer'}


@pytest.mark.parametrize('inputs', [
    {'mode': 'FragmentHEALER'}, {'mode': 'SiteHEALER'},
    {'mode': 'SiteHEALER', 'reactive_sites': [3]},
    {'mode': 'SiteHEALER', 'reactive_sites': [-1]},
    {'reactive_sites': [0]}, {'starting_smiles': 'invalid'},
    {'starting_smiles': 'CC.O'}, {'scaffold_smarts': 'c1ccccc1'},
    {'property_filters': [{'property': 'logp', 'minimum': 5, 'maximum': 1}]},
])
def test_invalid_requests_never_submit(api, inputs):
    operations, _, http, context = api
    with pytest.raises(ValueError):
        operations.enumerate({'starting_smiles': 'OCC', 'max_candidates': 16, **inputs}, context)
    context.submit_job.assert_not_called()
    http.assert_not_called()


def test_fragment_remote_adapter_rejects_without_http(api):
    _, remote, http, context = api
    with pytest.raises(ValueError, match='FragmentHEALER is not supported'):
        remote.submit({'mode': 'FragmentHEALER'}, context)
    http.assert_not_called()


@pytest.mark.parametrize('status,expected', [
    ('PENDING', 'running'), ('received', 'running'), (' STARTED ', 'running'),
    ('PROGRESS', 'running'), ('RETRY', 'running'),
    ('FAILURE', 'failed'), ('REVOKED', 'cancelled'),
])
def test_status_translation_and_quoted_job_id(api, status, expected):
    _, remote, http, context = api
    http.side_effect = None
    http.return_value = response({'status': status})
    assert remote.poll('a/b ?#%', {}, context) == {'status': expected}
    http.assert_called_once_with('GET', 'https://healer.example/api/jobs/a%2Fb%20%3F%23%25',
                                 headers={}, timeout=(5, 30), allow_redirects=False, stream=True)


def test_normalization_matches_local_and_retains_provenance(api):
    _, remote, http, context = api
    inputs = payload(api, seed=17, scaffold_smarts='CO', property_filters=[
        {'property': 'molecular_weight', 'minimum': 0, 'maximum': 90}])
    products = ['CCO', 'OCCC', 'CCCO', 'broken', 'CC.O', 'CCN', 'CCCCCCCCO', None]
    rows = [{'Product': value, 'Reaction': f'reaction-{i}'} for i, value in enumerate(products)]
    http.side_effect = None
    http.return_value = response({'job_id': 'actual-1', 'status': 'SUCCESS',
                                 'result': {'complete': rows, 'stats': {'seconds': 1.5}}})
    result = remote.poll('actual-1', inputs, context)
    assert result['status'] == 'succeeded'
    library = result['result']
    Draft202012Validator(MANIFEST['remote_jobs']['enumeration']['output_schema']).validate(library)
    candidates, exclusions = filter_library([v or '' for v in products], canonical('OCC'), inputs['constraints'])
    assert library['candidates'] == candidates
    assert [c['smiles'] for c in candidates] == ['CCCO']
    assert library['exclusions'] == exclusions
    assert {e['reason'] for e in exclusions} == {
        'seed_or_duplicate', 'invalid_structure', 'scaffold_constraint', 'property_constraint'}
    assert library['starting_smiles'] == 'CCO'
    provenance = library['provenance']
    assert provenance['reaction_rows'] == rows
    assert provenance['stats'] == {'seconds': 1.5}
    assert provenance['remote_job'] == {'service': 'healer-api', 'remote_id': 'actual-1',
                                        'endpoint': 'https://healer.example/api', 'status': 'SUCCESS'}
    assert provenance['requested_seed'] == 17 and provenance['seed_applied'] is False
    assert 'version' not in provenance


@pytest.mark.parametrize('rows,expected_count', [([], 0), ([{'Product': 'CCO'}], 0),
    ([{'Product': 'CCN'}, {'Product': 'CCC'}, {'Product': 'CCCC'}], 2)])
def test_empty_libraries_and_raw_cap(api, rows, expected_count):
    _, remote, http, context = api
    inputs = payload(api, max_candidates=2)
    http.side_effect = None
    http.return_value = response({'status': 'success', 'result': {'complete': rows}})
    library = remote.poll('job', inputs, context)['result']
    assert len(library['candidates']) == expected_count
    assert library['provenance']['reaction_rows'] == rows[:2]
    assert library['provenance']['source_count'] == len(rows)


@pytest.mark.parametrize('data', [[], {}, {'job_id': '', 'status': 'submitted'},
    {'job_id': 12, 'status': 'submitted'}, {'job_id': 'job', 'status': 'queued'}])
def test_bad_submission_responses(api, data):
    _, remote, http, context = api
    inputs = payload(api)
    http.side_effect = None
    http.return_value = response(data)
    with pytest.raises(ValueError):
        remote.submit(inputs, context)
    assert http.call_count == 1


@pytest.mark.parametrize('data', [[], {}, {'status': 'queued'}, {'status': 'partial'},
    {'status': 'SUCCESS'}, {'status': 'SUCCESS', 'result': {'complete': {}}},
    {'status': 'SUCCESS', 'result': {'complete': [{}]}},
    {'status': 'SUCCESS', 'result': {'complete': ['CCO']}},
    {'status': 'PROGRESS', 'job_id': 'different'}])
def test_bad_poll_responses(api, data):
    _, remote, http, context = api
    inputs = payload(api)
    http.side_effect = None
    http.return_value = response(data)
    with pytest.raises(ValueError):
        remote.poll('job', inputs, context)
    assert http.call_count == 1


@pytest.mark.parametrize('failure', ['timeout', 'http', 'json', 'redirect'])
@pytest.mark.parametrize('entrypoint', ['submit', 'poll'])
def test_http_failures_are_bounded_not_retried_or_leaked(api, failure, entrypoint):
    _, remote, http, context = api
    inputs = payload(api)
    http.side_effect = None
    reply = response({})
    http.return_value = reply
    if failure == 'timeout':
        http.side_effect = requests.Timeout('private credential')
    elif failure == 'http':
        reply.raise_for_status.side_effect = requests.HTTPError('private credential')
    elif failure == 'json':
        reply.iter_content.return_value = [b'private credential']
    else:
        reply.status_code = 302
    with pytest.raises((ValueError, RuntimeError)) as error:
        if entrypoint == 'submit':
            remote.submit(inputs, context)
        else:
            remote.poll('job', inputs, context)
    assert 'private credential' not in str(error.value)
    assert http.call_count == 1
    if failure != 'timeout':
        reply.close.assert_called_once()


@pytest.mark.parametrize('excess', [0, 1])
def test_stream_byte_limit_and_chunked_json(api, excess):
    _, remote, http, context = api
    http.side_effect = None
    reply = response({})
    http.return_value = reply
    body = b'{"status":"PROGRESS"}'
    body += b' ' * (remote.MAX_RESPONSE_BYTES - len(body) + excess)
    reply.iter_content.return_value = (body[i:i + 8192] for i in range(0, len(body), 8192))
    if excess:
        with pytest.raises(ValueError, match='invalid response'):
            remote.poll('job', {}, context)
    else:
        assert remote.poll('job', {}, context) == {'status': 'running'}
    assert http.call_count == 1
    reply.close.assert_called_once()


@pytest.mark.parametrize('chunks', [[b'{', b'"status":"PROGRESS"}'], [b'', b''], []])
def test_stream_total_deadline_including_empty_chunks(api, monkeypatch, chunks):
    _, remote, http, context = api
    http.side_effect = None
    reply = response({})
    http.return_value = reply
    now = [0.0]
    monkeypatch.setattr(remote.time, 'monotonic', lambda: now[0])

    def slow_chunks():
        for chunk in chunks:
            now[0] += 16
            yield chunk
        now[0] = 31

    reply.iter_content.return_value = slow_chunks()
    with pytest.raises(RuntimeError, match='request failed'):
        remote.poll('job', {}, context)
    assert http.call_count == 1
    reply.close.assert_called_once()


def test_stream_failure_closes_response_without_retry(api):
    _, remote, http, context = api
    http.side_effect = None
    reply = response({})
    http.return_value = reply

    def broken_chunks():
        yield b'{'
        raise requests.ConnectionError('private credential')

    reply.iter_content.return_value = broken_chunks()
    with pytest.raises(RuntimeError, match='^HEALER API request failed$'):
        remote.poll('job', {}, context)
    assert http.call_count == 1
    reply.close.assert_called_once()
