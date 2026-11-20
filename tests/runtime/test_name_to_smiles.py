"""Verify sourced name resolution, ambiguity handling, and campaign dispatch."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import json

import pytest
import requests
from jsonschema import validate

from sable_runtime.contracts import Action, Budget
from sable_runtime.executor import Executor, ServiceBinding
from sable_runtime.ledger import Ledger
from sable_runtime.registry import SkillRegistry

ROOT = Path(__file__).resolve().parents[2] / 'skills' / 'name-to-smiles'
SCHEMA = json.loads((ROOT / 'manifest.json').read_text())['operations'][0]['output_schema']
spec = importlib.util.spec_from_file_location('name_to_smiles_skill', ROOT / 'operations.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
CONTEXT = SimpleNamespace(service=ServiceBinding('https://example.test/rest/pug'))


class Response:
    def __init__(self, rows=None, status=200, raw=None):
        self.status_code = status
        self.raw = raw if raw is not None else json.dumps({'PropertyTable': {'Properties': rows}}).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def iter_content(self, chunk_size):
        yield self.raw


def mock_response(monkeypatch, response):
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return response
    monkeypatch.setattr(requests, 'post', post)
    return calls


def checked(name='ethanol'):
    result = module.resolve({'name': name}, CONTEXT)
    validate(result, SCHEMA)
    return result


def test_resolved_provenance_and_bounded_request(monkeypatch):
    calls = mock_response(monkeypatch, Response([{'CID': 702, 'SMILES': 'CCO', 'InChIKey': 'LFQSCWFLJHTTHZ-UHFFFAOYSA-N', 'IUPACName': 'ethanol'}]))
    result = checked('ethanol / test?')
    assert result['status'] == 'resolved'
    assert result['matches'][0]['source_url'] == 'https://pubchem.ncbi.nlm.nih.gov/compound/702'
    assert result['matches'][0]['smiles'] == 'CCO'
    assert result['retrieved_at'] and result['method_version']
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert 'ethanol' not in url
    assert kwargs['data']['name'] == 'ethanol / test?'
    assert kwargs['allow_redirects'] is False and kwargs['timeout'] == (5, 20)


@pytest.mark.parametrize('status,expected', [(404, 'not_found'), (429, 'unavailable'), (503, 'unavailable'), (302, 'unavailable')])
def test_http_failures_do_not_produce_structures(monkeypatch, status, expected):
    calls = mock_response(monkeypatch, Response(status=status))
    result = checked()
    assert result['status'] == expected and result['matches'] == []
    assert len(calls) == 1


def test_timeout(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.Timeout('private provider text')
    monkeypatch.setattr(requests, 'post', fail)
    assert checked()['status'] == 'unavailable'


def test_ambiguity_and_truncation(monkeypatch):
    mock_response(monkeypatch, Response([{'CID': index, 'SMILES': 'CCO'} for index in range(1, 23)]))
    result = checked()
    assert result['status'] == 'ambiguous' and result['truncated']
    assert len(result['matches']) == 20


@pytest.mark.parametrize('raw', [b'not json', b'{}', b'x' * 1_000_001, b'{"PropertyTable":{"Properties":[{"CID":1,"SMILES":"invalid"}]}}'])
def test_invalid_provider_output(monkeypatch, raw):
    mock_response(monkeypatch, Response(raw=raw))
    result = checked()
    assert result['status'] == 'invalid_response' and not result['matches']


def test_stereochemistry_salts_and_older_response_field(monkeypatch):
    mock_response(monkeypatch, Response([{'CID': 1, 'IsomericSMILES': 'C[C@H](O)C(=O)[O-].[Na+]'}]))
    result = checked()
    assert result['matches'][0]['components'] == 2
    assert '@' in result['matches'][0]['smiles']
    assert '[Na+]' in result['matches'][0]['smiles']


def test_registry_dispatch_and_durable_replay(monkeypatch, tmp_path):
    calls = mock_response(monkeypatch, Response([{'CID': 702, 'SMILES': 'CCO'}]))
    registry = SkillRegistry()
    registry.install(ROOT)
    assert ('name-to-smiles', '1.0.0') not in registry.available()
    assert ('name-to-smiles', '1.0.0') in registry.available({'pubchem'})
    skill = registry.get('name-to-smiles', '1.0.0')
    ledger = Ledger(tmp_path / 'campaign.sqlite')
    ledger.create_campaign('campaign', 'owner', Budget(max_actions=2, max_units=2))
    executor = Executor(registry, ledger, grants={'owner': {('name-to-smiles', '1.0.0', 'resolve')}}, services={'pubchem': CONTEXT.service})
    action = Action(campaign_id='campaign', action_id='lookup', skill_id='name-to-smiles', skill_version='1.0.0', skill_digest=skill.digest, operation_id='resolve', inputs={'name': 'ethanol'})
    result = executor.execute(action, owner='owner')
    assert result['status'] == 'resolved'
    assert executor.execute(action, owner='owner') == result
    assert len(calls) == 1


def test_cas_search_details_export_and_secret_isolation(monkeypatch, tmp_path):
    from pydantic import SecretStr
    from rdkit import Chem
    from sable_runtime.artifacts import ArtifactStore
    from sable_runtime.executor import ExecutionError
    from sable_runtime.skill_validation import validate_package
    root = ROOT.parent / 'cas-lookup'
    validate_package(root)
    registry = SkillRegistry(); registry.install(root)
    skill = registry.get('cas-lookup', '1.0.0')
    ledger = Ledger(tmp_path / 'cas.sqlite')
    ledger.create_campaign('campaign', 'alice', Budget(max_actions=8, max_units=8))
    artifacts = ArtifactStore(ledger)
    executor = Executor(registry, ledger, grants={'alice': {('cas-lookup','1.0.0',op.id) for op in skill.manifest.operations}},
        services={'cas-common-chemistry': ServiceBinding('https://commonchemistry.cas.org/direct-api','cas-common-chemistry')},
        credentials=lambda owner, reference: SecretStr('test-secret-cas-key'), artifacts=artifacts)
    detail = {'rn':'50-00-0','name':'Formaldehyde','uri':'substance/pt/50000','canonicalSmile':'C=O',
        'image':'<svg>untrusted markup</svg>',
        'experimentalProperties':[{'name':'Boiling Point','property':'-19.5 °C at 760 mmHg','sourceNumber':1},
                                  {'name':'Melting Point','property':'-92 to -90 °C','sourceNumber':1},
                                  {'name':'Density','property':'0.8 g/cm3','sourceNumber':1}],
        'propertyCitations':[{'sourceNumber':1,'source':'Reference source'}]}
    calls=[]
    def get(url, **kwargs):
        calls.append((url,kwargs))
        if url.endswith('/search'):return Response(raw=json.dumps({'count':'2','results':[{'rn':'50-00-0','name':'Formaldehyde'}]}).encode())
        if url.endswith('/export'):return Response(raw=Chem.MolToMolBlock(Chem.MolFromSmiles('C=O')).encode())
        return Response(raw=json.dumps(detail).encode())
    monkeypatch.setattr(requests,'get',get)
    def run(operation, action_id, inputs):
        return executor.execute(Action(campaign_id='campaign',action_id=action_id,skill_id='cas-lookup',skill_version='1.0.0',
            skill_digest=skill.digest,operation_id=operation,inputs=inputs),owner='alice')
    page=run('search','search',{'query':'form*','size':1})
    assert page['count']==2 and page['has_more'] and len(calls)==1
    record=run('detail','detail',{'cas_rn':'50-00-0'})
    assert record['record']['experimentalProperties']==detail['experimentalProperties']
    assert record['record']['propertyCitations']==detail['propertyCitations']
    assert 'image' not in record['record'] and record['smiles']=='C=O'
    assert run('detail','detail',{'cas_rn':'50-00-0'})==record and len(calls)==2
    exported=run('export_molfile','export',{'detail_action_id':'detail'})
    assert exported['status']=='resolved'
    assert 'M  END' in artifacts.read('campaign','alice',exported['artifact']['id'])
    with pytest.raises(PermissionError):artifacts.read('campaign','bob',exported['artifact']['id'])
    with pytest.raises(ExecutionError):run('export_molfile','foreign',{'detail_action_id':'not-owned'})
    assert len(calls)==3
    for _,kwargs in calls:
        assert kwargs['headers']['X-API-KEY']=='test-secret-cas-key'
        assert kwargs['headers']['x-origin']=='https://commonchemistry.cas.org/api-overview'
        assert kwargs['allow_redirects'] is False and kwargs['timeout']==(5,20)
    assert calls[-1][1]['params']['uri']=='substance/pt/50000'
    assert 'test-secret-cas-key' not in json.dumps(ledger.events('campaign','alice'))


def test_cas_failures_do_not_establish_identity(monkeypatch):
    from pydantic import SecretStr
    spec=importlib.util.spec_from_file_location('cas_lookup',ROOT.parent/'cas-lookup/operations.py')
    cas=importlib.util.module_from_spec(spec);spec.loader.exec_module(cas)
    context=SimpleNamespace(service=ServiceBinding('https://commonchemistry.cas.org/direct-api'),secret=SecretStr('test-key'))
    calls=[]
    for status,expected in [(401,'authentication_required'),(403,'authentication_required'),(404,'not_found'),(429,'rate_limited'),(503,'unavailable')]:
        def get(*args,**kwargs):calls.append(args);return Response(status=status)
        monkeypatch.setattr(requests,'get',get)
        result=cas.detail({'cas_rn':'50-00-0'},context)
        assert result['status']==expected and result['record'] is None
    assert len(calls)==5
    with pytest.raises(ValueError):cas.detail({'cas_rn':'50-00-1'},context)
    assert len(calls)==5
    monkeypatch.setattr(requests,'get',lambda *a,**kw:Response(raw=b'{"rn":"64-17-5","name":"wrong identity"}'))
    assert cas.detail({'cas_rn':'50-00-0'},context)['status']=='invalid_response'
