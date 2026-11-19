"""Sourced target resolution, identity checks, and durable campaign dispatch."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import requests
from jsonschema import validate
from sable_runtime.contracts import Action, Budget
from sable_runtime.executor import Executor, ServiceBinding
from sable_runtime.ledger import Ledger
from sable_runtime.registry import SkillRegistry
from sable_runtime.skill_validation import validate_package

ROOT = Path(__file__).resolve().parents[2] / 'skills/uniprot'
spec = importlib.util.spec_from_file_location('uniprot_skill', ROOT / 'operations.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
CONTEXT = SimpleNamespace(service=ServiceBinding('https://rest.uniprot.org'))
RECORD = {'primaryAccession': 'P21453', 'sequence': {'value': 'MACK', 'length': 4},
          'proteinDescription': {'recommendedName': {'fullName': {'value': 'Test receptor'}}},
          'organism': {'scientificName': 'Homo sapiens'}, 'entryAudit': {'sequenceVersion': 2}}


class Response:
    def __init__(self, data=RECORD, status=200, raw=None):
        self.status_code = status
        self.raw = json.dumps(data).encode() if raw is None else raw
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, chunk_size): yield self.raw


def mock(monkeypatch, response):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return response
    monkeypatch.setattr(requests, 'get', get)
    return calls


def test_sequence_identity_provenance_and_request_bounds(monkeypatch):
    calls = mock(monkeypatch, Response())
    result = module.resolve({'accession': 'p21453'}, CONTEXT)
    skill = validate_package(ROOT)
    validate(result, skill.manifest.operations[0].output_schema)
    assert result['sequence'] == 'MACK' and result['organism'] == 'Homo sapiens'
    assert result['sequence_version'] == 2 and result['retrieved_at']
    assert calls == [('https://rest.uniprot.org/uniprotkb/P21453.json',
                      {'timeout': (5, 20), 'allow_redirects': False, 'stream': True})]


@pytest.mark.parametrize('accession', ['P21453-2', '../secret', 'https://example.com', 'invalid'])
def test_rejects_unsupported_identifiers(monkeypatch, accession):
    calls = mock(monkeypatch, Response())
    with pytest.raises(ValueError): module.resolve({'accession': accession}, CONTEXT)
    assert not calls


@pytest.mark.parametrize('record', [
    {**RECORD, 'primaryAccession': 'P12345'},
    {**RECORD, 'sequence': {'value': 'MACK', 'length': 5}},
    {**RECORD, 'sequence': {'value': 'MA<CK', 'length': 5}},
])
def test_rejects_wrong_identity_or_sequence(monkeypatch, record):
    mock(monkeypatch, Response(record))
    with pytest.raises(ValueError): module.resolve({'accession': 'P21453'}, CONTEXT)


@pytest.mark.parametrize('status', [404, 429, 503, 302])
def test_http_failure_no_fabricated_target(monkeypatch, status):
    calls = mock(monkeypatch, Response(status=status))
    with pytest.raises((ValueError, LookupError)): module.resolve({'accession': 'P21453'}, CONTEXT)
    assert len(calls) == 1


def test_response_size_and_json_validation(monkeypatch):
    for raw in [b'x' * 3_000_001, b'not json']:
        mock(monkeypatch, Response(raw=raw))
        with pytest.raises(ValueError): module.resolve({'accession': 'P21453'}, CONTEXT)


def test_dispatch_and_replay(monkeypatch, tmp_path):
    calls = mock(monkeypatch, Response())
    registry = SkillRegistry(); registry.install(ROOT)
    skill = registry.get('uniprot', '1.0.0')
    ledger = Ledger(tmp_path / 'test.sqlite')
    ledger.create_campaign('campaign', 'alice', Budget(max_actions=2, max_units=2))
    executor = Executor(registry, ledger, grants={'alice': {('uniprot', '1.0.0', 'resolve')}}, services={'uniprot': CONTEXT.service})
    action = Action(campaign_id='campaign', action_id='resolve-target', skill_id='uniprot', skill_version='1.0.0', skill_digest=skill.digest, operation_id='resolve', inputs={'accession': 'P21453'})
    first = executor.execute(action, owner='alice')
    assert executor.execute(action, owner='alice') == first
    assert len(calls) == 1
