"""Offline provider contracts, scientific invariants and durable package integration."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from jsonschema import Draft202012Validator, ValidationError
from pydantic import SecretStr
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from sable_runtime.registry import SkillRegistry


ROOT = Path(__file__).resolve().parents[2] / 'skills'
PACKAGES = ('stoplight', 'boltz', 'boltz-platform', 'openfe')


def module(skill, file='remote'):
    spec = importlib.util.spec_from_file_location(skill.replace('-', '_') + '_' + file,
                                                 ROOT / skill / (file + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def manifest(skill):
    return json.loads((ROOT / skill / 'manifest.json').read_text())


def validate(skill, job, output):
    if output['status'] in {'succeeded', 'partial'}:
        schema = manifest(skill)['remote_jobs'][job]['output_schema']
        Draft202012Validator(schema).validate(output['result'])
        json.dumps(output, allow_nan=False)
    return output


def context(**kwargs):
    return SimpleNamespace(service=SimpleNamespace(endpoint='https://provider.invalid'),
                           secret=SecretStr('private-fixture-token'), results={},
                           campaign_id='owned', action_id='create-one', **kwargs)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Live HTTP is forbidden in these tests')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)


@pytest.mark.parametrize('skill', PACKAGES)
def test_packages_install_and_every_schema_is_closed(skill):
    registry = SkillRegistry()
    registry.install(ROOT / skill)
    package = registry.get(skill, manifest(skill)['version'])
    assert 'remote.py' in package.files
    assert package.manifest.requirements.services == [skill]
    def walk(value):
        if isinstance(value, dict):
            if value.get('type') == 'object':
                assert value.get('additionalProperties') is False
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(manifest(skill))


@pytest.mark.parametrize('skill', PACKAGES)
def test_http_single_attempt_bounded_and_sanitized(skill, monkeypatch):
    remote = module(skill)
    calls, settings = [], []
    real_client = httpx.Client
    def respond(request):
        calls.append(request)
        return httpx.Response(503, text='private-fixture-token')
    class client(real_client):
        def __init__(self, **kwargs):
            settings.append(kwargs.copy())
            kwargs['transport'].close()
            kwargs['transport'] = httpx.MockTransport(respond)
            super().__init__(**kwargs)
    monkeypatch.setattr(remote.httpx, 'Client', client)
    with pytest.raises(RuntimeError) as error:
        if skill == 'boltz-platform':
            remote._call(context(), 'retrieve', 'job-a')
        else:
            remote._request(context(), 'POST', '/submit', json={})
    assert len(calls) == 1
    assert 'private-fixture-token' not in str(error.value)
    if skill == 'boltz-platform':
        assert calls[0].headers['x-api-key'] == 'private-fixture-token'
    else:
        assert calls[0].headers['Authorization'] == 'Bearer private-fixture-token'
    assert settings[0]['follow_redirects'] is False
    assert settings[0]['timeout'].connect == 5
    assert settings[0]['timeout'].read == 20


def test_stoplight_terminal_csv_identity_partial_and_native_names(monkeypatch):
    remote = module('stoplight')
    request = Mock(return_value=b'SMILES,HBD,Solubility in Water (mg/L),ALogP\nCCO,1,12.5,NaN\nCC,N/A,0,2\n')
    monkeypatch.setattr(remote, '_request', request)
    payload = {'molecules': [{'id': 'a', 'smiles': 'CCO'}, {'id': 'b', 'smiles': 'CC'}]}
    result = validate('stoplight', 'properties', remote.submit(payload, context()))
    assert result['status'] == 'partial'
    assert result['result']['measurements'][0]['properties'] == {'HBD': 1, 'Solubility in Water (mg/L)': 12.5}
    assert result['result']['exclusions'][0]['id'] == 'b'
    assert request.call_args.args[1:] == ('POST', '/smiles-csv')
    assert request.call_args.kwargs['json']['options']['precision'] == '2'


@pytest.mark.parametrize('csv', ['SMILES,HBD\nCC,1\n', 'SMILES,HBD\nCCO,1\nCC,2\n',
                                  'SMILES,HBD,HBD\nCCO,1,2\n', 'HBD\n1\n'])
def test_stoplight_never_guesses_row_identity(csv, monkeypatch):
    remote = module('stoplight')
    monkeypatch.setattr(remote, '_request', lambda *a, **k: csv.encode())
    with pytest.raises(ValueError):
        remote.submit({'molecules': [{'id': 'a', 'smiles': 'CCO'}]}, context())


def screen_payload():
    return {'molecules': [{'id': 'a', 'smiles': 'CCO'}], 'proteins': [{'sequence': 'MKTV', 'chain_id': 'A'}],
            'protein_scope_id': 'scope', 'library_action_id': 'library'}


def screen_row(**kwargs):
    return {'id': 'result-a', 'external_id': 'a', 'smiles': 'CCO',
            'metrics': {'optimization_score': 4.2, 'complex_plddt': 0.91}, 'warnings': [], **kwargs}


def test_platform_request_and_scientific_semantics(monkeypatch):
    remote = module('boltz-platform')
    request = Mock(side_effect=[{'id': 'job-a'}, {'status': 'succeeded'}, {'data': [screen_row()], 'has_more': False}])
    monkeypatch.setattr(remote, '_call', request)
    assert remote.submit(screen_payload(), context()) == {'remote_id': 'job-a'}
    assert request.call_args.kwargs['target']['entities'][0]['value'] == 'MKTV'
    output = validate('boltz-platform', 'screen', remote.poll('job-a', screen_payload(), context()))
    metrics = output['result']['measurements'][0]['metrics']
    assert metrics == {'optimization_score': 4.2, 'complex_plddt': 0.91}
    assert 'affinity' not in metrics


@pytest.mark.parametrize('row', [screen_row(smiles='CC'), screen_row(external_id='other'),
                                screen_row(metrics={'complex_plddt': 91}),
                                screen_row(metrics={'optimization_score': float('nan')})])
def test_platform_rejects_identity_and_invalid_metrics(row, monkeypatch):
    remote = module('boltz-platform')
    monkeypatch.setattr(remote, '_call', Mock(side_effect=[{'status': 'succeeded'}, {'data': [row], 'has_more': False}]))
    with pytest.raises(ValueError):
        remote.poll('job-a', screen_payload(), context())


def test_platform_pagination_is_bounded_and_cancel_requires_confirmation(monkeypatch):
    remote = module('boltz-platform')
    request = Mock(side_effect=[{'status': 'succeeded'}, {'data': [], 'has_more': True, 'last_id': 'one'},
                               {'data': [], 'has_more': True, 'last_id': 'two'}])
    monkeypatch.setattr(remote, '_call', request)
    with pytest.raises(ValueError, match='bounds'):
        remote.poll('job-a', screen_payload(), context())
    assert request.call_count == 3
    request.side_effect = [{'status': 'running'}, {'status': 'stopped'}]
    assert remote.cancel('job-a', {}, context()) == {'status': 'running'}
    assert remote.cancel('job-a', {}, context()) == {'status': 'cancelled'}


def test_self_hosted_prediction_requires_completion_and_keeps_affinity(monkeypatch):
    remote = module('boltz')
    payload = screen_payload()
    payload['proteins'][0]['chain_id'] = 'L'
    request = Mock(side_effect=[{'job_id': 'job-a'}, {'status': 'running', 'affinity': -9},
                               {'status': 'completed', 'affinity': {'affinity_pred_value': -2.5},
                                'confidence': {'confidence_score': .8}}])
    monkeypatch.setattr(remote, '_request', request)
    assert remote.submit_prediction(payload, context()) == {'remote_id': 'job-a'}
    document = json.loads(request.call_args.kwargs['json']['yaml_text'])
    assert document['properties'][0]['affinity']['binder'] == 'LL'
    assert remote.poll_prediction('job-a', payload, context()) == {'status': 'running'}
    result = validate('boltz', 'prediction', remote.poll_prediction('job-a', payload, context()))
    assert result['result']['measurement']['metrics'] == {'affinity': -2.5, 'binding_confidence': .8}


def openfe_payload(protocol='rbfe'):
    ops = module('openfe', 'operations')
    return {'campaign_name': ops._name('owned', 'create-one'), 'origin_action_id': 'create-one',
            'protocol': protocol, 'ligands': ['ref', 'candidate'], 'reference': None if protocol == 'abfe' else 'ref',
            'repeats': 3, 'stage': 'submit', 'allow_failed_quality': False}


def summary(payload, state='done'):
    return {'name': payload['campaign_name'], 'protocol': payload['protocol'],
            'last_operation': {'operation': payload['stage'], 'status': 'done'},
            'runs': [{'name': 'edge', 'state': state, 'repeats': 3}]}


def network():
    return {'edges': [{'edge': 'edge', 'ligand_a': 'ref', 'ligand_b': 'candidate', 'ddg': -2., 'uncertainty': .2}],
            'ligands': [{'ligand': 'ref', 'dg_relative': 1., 'uncertainty': .1},
                        {'ligand': 'candidate', 'dg_relative': -1., 'uncertainty': .15}],
            'cycles': [], 'unreachable': [], 'checks': []}


def ligand_sdf(smiles='CCO'):
    molecule = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(molecule, randomSeed=42) == 0
    return Chem.MolToMolBlock(molecule) + '\n$$$$\n'


@pytest.mark.parametrize('case', ['unreadable', 'multiple', 'graph', 'stereo', 'no_coordinates'])
def test_openfe_rejects_bad_coordinates_before_queue(case):
    ops = module('openfe', 'operations')
    smiles, sdf = 'CCO', ligand_sdf()
    if case == 'unreadable': sdf = 'not an SDF'
    if case == 'multiple': sdf += ligand_sdf()
    if case == 'graph': sdf = ligand_sdf('CCN')
    if case == 'stereo':
        smiles = 'C[C@H](O)F'
        sdf = ligand_sdf('C[C@@H](O)F')
    if case == 'no_coordinates': sdf = Chem.MolToMolBlock(Chem.MolFromSmiles('CCO')) + '\n$$$$\n'
    queued = Mock()
    ctx = context(submit_job=queued, read_artifact=lambda id: 'ATOM  fixture' if id == 'protein' else sdf)
    with pytest.raises(ValueError):
        ops.create({'protocol': 'abfe', 'protein_artifact_id': 'protein',
                    'ligands': [{'id': 'ligand', 'smiles': smiles, 'artifact_id': 'ligand'}], 'repeats': 3}, ctx)
    queued.assert_not_called()


def test_openfe_accepts_explicit_hydrogens_and_specified_stereo():
    ops = module('openfe', 'operations')
    ops._validate_ligand(ligand_sdf('C[C@H](O)F'), 'C[C@H](O)F')


def test_openfe_artifact_resolution_owned_names_and_explicit_sequence(monkeypatch):
    ops = module('openfe', 'operations')
    queued = []
    sdf = ligand_sdf()
    ctx = context(read_artifact=lambda id: {'protein': 'ATOM  fixture', 'ligand': sdf}[id],
                  submit_job=lambda name, payload: queued.append((name, payload)) or {'kind': 'remote_job', 'job_id': 'local', 'status': 'queued'})
    inputs = {'protocol': 'abfe', 'protein_artifact_id': 'protein', 'ligands': [{'id': 'ref', 'smiles': 'CCO', 'artifact_id': 'ligand'}], 'repeats': 3}
    ops.create(inputs, ctx)
    name, payload = queued.pop()
    assert name == 'create' and payload['protein_pdb'] == 'ATOM  fixture'
    assert payload['ligands'][0]['sdf'] == sdf
    assert 'protein_artifact_id' not in payload
    with pytest.raises(ValueError, match='owned'):
        ops.advance({'previous_action_id': 'arbitrary-provider-name', 'stage': 'prep'}, ctx)
    record = module('openfe')._record({**openfe_payload(), 'stage': 'created'})
    ctx.results['created'] = record
    ops.advance({'previous_action_id': 'created', 'stage': 'prep'}, ctx)
    assert queued[-1][1]['stage'] == 'prep'
    with pytest.raises(ValueError, match='phases'):
        ops.advance({'previous_action_id': 'created', 'stage': 'submit'}, ctx)
    ctx.results['created'] = {**record, 'campaign_name': 'sable_' + '0' * 32}
    with pytest.raises(ValueError, match='belong'):
        ops.advance({'previous_action_id': 'created', 'stage': 'prep'}, ctx)


def test_openfe_upload_and_readonly_recovery(monkeypatch):
    remote = module('openfe')
    payload = {**openfe_payload(), 'protein_pdb': 'ATOM fixture', 'ligands': [
        {'id': 'ref', 'smiles': 'CCO', 'sdf': ligand_sdf()}, {'id': 'candidate', 'smiles': 'CC', 'sdf': ligand_sdf('CC')}]}
    request = Mock(return_value=summary(payload))
    monkeypatch.setattr(remote, '_request', request)
    created = validate('openfe', 'create', remote.submit_create(payload, context()))
    posted = json.loads(request.call_args.kwargs['data']['campaign'])
    assert posted['alignment'] == {'pose': 'keep'}
    assert posted['network'] == {'method': 'radial', 'central_ligand': 'ref'}
    assert len(request.call_args.kwargs['files']) == 3
    recovered = validate('openfe', 'create', remote.poll_create(payload['campaign_name'], payload, context()))
    assert recovered == created
    assert request.call_args.args[1:] == ('GET', '/campaigns/' + payload['campaign_name'])


def test_openfe_poll_never_advances_or_gathers(monkeypatch):
    remote = module('openfe')
    payload = openfe_payload()
    request = Mock(return_value=summary(payload))
    monkeypatch.setattr(remote, '_request', request)
    output = validate('openfe', 'stage', remote.poll_stage(payload['campaign_name'], payload, context()))
    assert output['result']['kind'] == 'openfe_campaign'
    assert request.call_count == 1
    assert request.call_args.args[1:] == ('GET', '/campaigns/' + payload['campaign_name'])


@pytest.mark.parametrize('protocol', ['rbfe', 'septop'])
def test_openfe_network_rebases_means_without_inventing_difference_uncertainty(protocol, monkeypatch):
    remote = module('openfe')
    payload = openfe_payload(protocol)
    request = Mock(side_effect=[summary(payload), network(), summary(payload)])
    monkeypatch.setattr(remote, '_request', request)
    output = validate('openfe', 'collect', remote.poll_collect(payload['campaign_name'], payload, context()))
    rows = output['result']['measurements']
    assert [row['value'] for row in rows] == [0, -2]
    assert rows[1]['fit_value'] == -1
    assert rows[1]['uncertainty'] == .15
    assert rows[1]['uncertainty_scope'] == 'network_mean_fit_not_reference_difference'
    assert output['result']['protocol'] == protocol
    assert request.call_count == 3


def test_openfe_pending_has_no_scientific_measurements(monkeypatch):
    remote = module('openfe')
    payload = openfe_payload()
    monkeypatch.setattr(remote, '_request', Mock(side_effect=[summary(payload), network(), summary(payload, 'running')]))
    output = validate('openfe', 'collect', remote.poll_collect(payload['campaign_name'], payload, context()))
    assert output == {'status': 'running'}


def test_openfe_collect_submits_without_gather_then_repeats_only_materialization(monkeypatch):
    remote = module('openfe')
    payload = openfe_payload()
    request = Mock(side_effect=[summary(payload), summary(payload), network(), summary(payload, 'running'),
                               summary(payload), network(), summary(payload)])
    monkeypatch.setattr(remote, '_request', request)
    assert remote.submit_collect(payload, context()) == {'remote_id': payload['campaign_name']}
    assert request.call_count == 1
    assert request.call_args.args[1:] == ('GET', '/campaigns/' + payload['campaign_name'])
    assert remote.poll_collect(payload['campaign_name'], payload, context()) == {'status': 'running'}
    completed = validate('openfe', 'collect', remote.poll_collect(payload['campaign_name'], payload, context()))
    assert completed['status'] == 'succeeded'
    assert request.call_count == 7
    assert all(call.args[1] == 'GET' for call in request.call_args_list)
    with pytest.raises(ValueError, match='owned'):
        remote.poll_collect('other-campaign', payload, context())
    assert request.call_count == 7


@pytest.mark.parametrize('failure', ['reference', 'partial_edge', 'duplicate', 'nan', 'repeats'])
def test_openfe_rejects_unusable_network_evidence(failure, monkeypatch):
    remote = module('openfe')
    payload, data = openfe_payload(), network()
    status = summary(payload)
    if failure == 'reference': data['unreachable'] = ['ref']
    if failure == 'partial_edge':
        status['runs'].append({'name': 'bad-edge', 'state': 'failed', 'repeats': 3})
        data['edges'].append({**data['edges'][0], 'edge': 'bad-edge'})
    if failure == 'duplicate': data['ligands'].append(data['ligands'][0])
    if failure == 'nan': data['ligands'][0]['uncertainty'] = float('nan')
    if failure == 'repeats': status['runs'][0]['repeats'] = 2
    monkeypatch.setattr(remote, '_request', Mock(side_effect=[status, data, status]))
    with pytest.raises(ValueError):
        remote.poll_collect(payload['campaign_name'], payload, context())


def test_openfe_quality_diagnostics_and_partial_exclusions(monkeypatch):
    remote = module('openfe')
    payload, data = openfe_payload(), network()
    payload['ligands'].append('disconnected')
    data['checks'] = [{'status': 'fail'}]
    request = Mock(side_effect=[summary(payload), data, summary(payload)] * 2)
    monkeypatch.setattr(remote, '_request', request)
    rejected = remote.poll_collect(payload['campaign_name'], payload, context())
    assert rejected == {'status': 'failed', 'diagnostics': {'reason': 'quality_checks_failed', 'quality_warnings': ['Failed network quality checks']}}
    payload['allow_failed_quality'] = True
    accepted = validate('openfe', 'collect', remote.poll_collect(payload['campaign_name'], payload, context()))
    assert accepted['status'] == 'partial'
    assert accepted['result']['exclusions'][0]['id'] == 'disconnected'


def test_openfe_abfe_absolute_repeats_and_units(monkeypatch):
    remote = module('openfe')
    payload = openfe_payload('abfe')
    status = summary(payload)
    status['runs'] = [{'name': name, 'state': 'done', 'repeats': 3} for name in payload['ligands']]
    results = [{'ligand': name, 'dg': -4., 'uncertainty': .2, 'repeats': 3, 'quality': 'pass'} for name in payload['ligands']]
    monkeypatch.setattr(remote, '_request', Mock(side_effect=[status, results, status]))
    output = validate('openfe', 'collect', remote.poll_collect(payload['campaign_name'], payload, context()))
    assert output['result']['reference'] is None
    assert output['result']['quantity'] == 'absolute_binding_free_energy'
    assert output['result']['units'] == 'kcal/mol'
    assert output['result']['measurements'][0]['value'] == -4
    invalid = deepcopy(output['result'])
    invalid['reference'] = 'ref'
    with pytest.raises(ValidationError):
        Draft202012Validator(manifest('openfe')['remote_jobs']['collect']['output_schema']).validate(invalid)


def test_queue_operations_never_contact_remote_handlers():
    ctx = context(submit_job=Mock(return_value={'kind': 'remote_job', 'job_id': 'local', 'status': 'queued'}))
    result = module('boltz', 'operations').predict({'molecule': {'id': 'a', 'smiles': 'CC'},
                                                  'proteins': [{'chain_id': 'A', 'sequence': 'mktv'}]}, ctx)
    assert result['job_id'] == 'local'
    assert ctx.submit_job.call_args.args[1]['proteins'][0]['sequence'] == 'MKTV'


def test_real_runtime_queues_then_drives_stoplight_once(tmp_path, monkeypatch):
    from sable_runtime.contracts import Action, Budget
    from sable_runtime.executor import Executor, ServiceBinding
    from sable_runtime.jobs import JobManager
    from sable_runtime.ledger import Ledger

    requests = []
    def respond(transport, request):
        requests.append(request)
        return httpx.Response(200, content=b'SMILES,HBD,ALogP\nCCO,1,-0.3\n')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', respond)
    registry = SkillRegistry()
    digest = registry.install(ROOT / 'stoplight')
    ledger = Ledger(tmp_path / 'jobs.sqlite')
    ledger.create_campaign('owned', 'alice', Budget(max_actions=3, max_units=3))
    bindings = {'stoplight': ServiceBinding('https://stoplight.invalid')}
    jobs = JobManager(ledger, registry, services=bindings)
    executor = Executor(registry, ledger, services=bindings, jobs=jobs,
                        grants={'alice': {('stoplight', '1.0.0', 'characterize')}})
    action = Action(campaign_id='owned', action_id='measure', skill_id='stoplight', skill_version='1.0.0',
                    skill_digest=digest, operation_id='characterize',
                    inputs={'molecules': [{'id': 'ethanol', 'smiles': 'CCO'}]})
    receipt = executor.execute(action, owner='alice')
    assert requests == []
    assert receipt['kind'] == 'remote_job'
    finished = jobs.drive('owned', receipt['job_id'], 'alice')
    assert finished['status'] == 'succeeded'
    assert finished['result']['measurements'][0]['properties']['ALogP'] == -.3
    jobs.drive('owned', receipt['job_id'], 'alice')
    executor.execute(action, owner='alice')
    assert len(requests) == 1


@pytest.mark.parametrize('provider', ['self_hosted', 'platform'])
def test_boltz_structure_retention_and_download_boundaries(provider, monkeypatch):
    from sable_runtime.structures import retain_structure
    stored, requests = [], []
    def write(name, content):
        stored.append((name, content))
        return {'id': 'artifact-test', 'name': name, 'size_bytes': len(content), 'sha256': 'a'*64}
    def respond(transport, request):
        requests.append(request)
        return httpx.Response(200, text='data_model\n_atom_site.id\n1\n')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', respond)
    ctx = context(write_artifact=write)
    url = '/files/model.cif' if provider == 'self_hosted' else 'https://api.boltz.bio/model.cif?signature=private'
    remote = module('boltz' if provider == 'self_hosted' else 'boltz-platform')
    if provider == 'self_hosted':
        monkeypatch.setattr(remote, '_request', lambda *a, **k: {'status':'completed', 'affinity':-2, 'cif_url':url})
        output = validate('boltz','prediction',remote.poll_prediction('job-a',screen_payload(),ctx))
        measurement = output['result']['measurement']
    else:
        monkeypatch.setattr(remote, '_call', Mock(side_effect=[{'status':'succeeded'}, {'data':[screen_row(artifacts={'structure':{'url':url}})],'has_more':False}]))
        output = validate('boltz-platform','screen',remote.poll('job-a',screen_payload(),ctx))
        measurement = output['result']['measurements'][0]
    assert measurement['structure']['id'] == 'artifact-test' and stored[0][0].endswith('.cif')
    assert bool(requests[0].headers.get('Authorization')) == (provider == 'self_hosted')
    assert 'signature' not in json.dumps(output)
    for bad in ['https://evil.invalid/model.cif', 'https://127.0.0.1/model.cif']:
        assert 'structure_status' in retain_structure(bad,ctx,provider=provider,name='bad')
    assert len(requests) == 1
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *a: httpx.Response(302,headers={'location':'https://evil.invalid'}))
    assert 'structure_status' in retain_structure(url,ctx,provider=provider,name='redirect')
    assert len(stored) == 1
