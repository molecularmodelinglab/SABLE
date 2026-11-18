from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil

import pytest
from pydantic import SecretStr

from sable_runtime.contracts import Action, Budget
from sable_runtime.executor import Executor, ExecutionError
from sable_runtime.ledger import Ledger, LedgerError
from sable_runtime.registry import SkillRegistry

SAMPLE = Path(__file__).resolve().parents[2] / 'skills/dataset-summary'
KEY = ('dataset-summary', '1.0.0', 'summarize')


@pytest.fixture
def runtime(tmp_path):
    package = tmp_path / 'skill'
    shutil.copytree(SAMPLE, package)
    registry = SkillRegistry()
    digest = registry.install(package)
    ledger = Ledger(tmp_path / 'ledger.sqlite')
    ledger.create_campaign('campaign', 'alice', Budget(max_actions=2, max_units=2))
    executor = Executor(registry, ledger, grants={'alice': {KEY}, 'bob': {KEY}})
    action = Action(campaign_id='campaign', action_id='one', skill_id=KEY[0],
                    skill_version=KEY[1], skill_digest=digest, operation_id=KEY[2],
                    inputs={'values': [2, 4, 6], 'unit': 'mg/mL'})
    return package, registry, ledger, executor, action


def test_install_discover_execute_and_replay_after_restart(runtime):
    package, registry, ledger, executor, action = runtime
    assert registry.discover(set()) == []
    assert 'instructions' not in registry.discover({KEY[:2]})[0]
    assert 'instructions' in registry.describe(*KEY[:2], allowed={KEY[:2]})
    result = executor.execute(action, owner='alice')
    assert result == {'count': 3, 'minimum': 2, 'maximum': 6, 'mean': 4, 'unit': 'mg/mL'}
    restarted = Executor(registry, Ledger(ledger.path), grants={'alice': {KEY}})
    assert restarted.execute(action, owner='alice') == result
    events = ledger.events('campaign', 'alice')
    assert [e['kind'] for e in events] == ['action_started', 'action_succeeded']
    assert len(ledger.events('campaign', 'alice', after=events[0]['sequence'])) == 1


def test_installation_snapshot_survives_source_changes(runtime):
    package, registry, ledger, executor, action = runtime
    (package / 'operations.py').write_text('raise RuntimeError("changed")')
    assert executor.execute(action, owner='alice')['mean'] == 4
    with pytest.raises(ValueError):
        registry.install(package)


@pytest.mark.parametrize('inputs', [{'values': [], 'unit': 'x'}, {'values': [float('nan')], 'unit': 'x'},
                                   {'values': [True], 'unit': 'x'}, {'values': [1], 'unit': 'x', 'extra': 1}])
def test_invalid_inputs_do_not_consume_budget(runtime, inputs):
    _, _, ledger, executor, action = runtime
    with pytest.raises(ExecutionError):
        executor.execute(action.model_copy(update={'inputs': inputs}), owner='alice')
    assert ledger.events('campaign', 'alice') == []


def test_ownership_permissions_and_digest(runtime):
    _, _, ledger, executor, action = runtime
    for owner in ('bob', 'ungranted'):
        with pytest.raises(PermissionError):
            executor.execute(action, owner=owner)
    with pytest.raises(PermissionError):
        ledger.events('campaign', 'bob')
    with pytest.raises(ExecutionError):
        executor.execute(action.model_copy(update={'skill_digest': '0' * 64}), owner='alice')


def test_budget_conflicts_and_concurrent_admission(runtime):
    _, _, ledger, executor, action = runtime
    executor.execute(action, owner='alice')
    with pytest.raises(LedgerError):
        executor.execute(action.model_copy(update={'inputs': {'values': [9], 'unit': 'x'}}), owner='alice')
    def attempt(index):
        try:
            executor.execute(action.model_copy(update={'action_id': f'action-{index}'}), owner='alice')
            return True
        except LedgerError:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(4))) == 1
    assert len(ledger.events('campaign', 'alice')) == 4


def test_running_action_is_not_resubmitted(runtime):
    _, _, ledger, executor, action = runtime
    from hashlib import sha256
    fingerprint = sha256(json.dumps(action.model_dump(), sort_keys=True, allow_nan=False).encode()).hexdigest()
    ledger.claim(action, 'alice', fingerprint, 1, {})
    with pytest.raises(LedgerError, match='reconciliation'):
        executor.execute(action, owner='alice')


@pytest.mark.parametrize('behavior', ['raise RuntimeError(context.secret.get_secret_value())',
                                      'return {"leak": context.secret.get_secret_value()}',
                                      'return {"wrong": 42}'])
def test_credentials_and_invalid_outputs_never_enter_events(tmp_path, behavior):
    package = tmp_path / 'skill'
    shutil.copytree(SAMPLE, package)
    manifest = json.loads((package / 'manifest.json').read_text())
    operation = manifest['operations'][0]
    operation['credential'] = 'provider-token'
    operation['output_schema'] = {'type': 'object', 'additionalProperties': False,
                                  'required': ['leak'], 'properties': {'leak': {'type': 'string'}}}
    (package / 'manifest.json').write_text(json.dumps(manifest))
    (package / 'operations.py').write_text(f'def summarize(inputs, context):\n    {behavior}\n')
    registry = SkillRegistry()
    digest = registry.install(package)
    ledger = Ledger(tmp_path / 'db.sqlite')
    ledger.create_campaign('campaign', 'alice', Budget(max_actions=2, max_units=2))
    def resolve(owner, reference):
        assert (owner, reference) == ('alice', 'provider-token')
        return SecretStr('private-token')
    executor = Executor(registry, ledger, grants={'alice': {KEY}}, credentials=resolve)
    action = Action(campaign_id='campaign', action_id='one', skill_id=KEY[0], skill_version=KEY[1],
                    skill_digest=digest, operation_id=KEY[2], inputs={'values': [1], 'unit': 'x'})
    with pytest.raises(ExecutionError) as error:
        executor.execute(action, owner='alice')
    assert 'private-token' not in str(error.value)
    assert 'private-token' not in json.dumps(ledger.events('campaign', 'alice'))
    with pytest.raises(LedgerError):
        executor.execute(action, owner='alice')


def test_external_schema_references_are_rejected(tmp_path):
    package = tmp_path / 'skill'
    shutil.copytree(SAMPLE, package)
    manifest = json.loads((package / 'manifest.json').read_text())
    manifest['operations'][0]['input_schema']['properties']['unit'] = {'$ref': 'https://example.test/schema'}
    (package / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='local references'):
        SkillRegistry().install(package)


def test_credential_bridge_enforces_owner_provider_and_status():
    from contextlib import contextmanager
    from types import SimpleNamespace
    from sable_runtime.credentials import ProviderCredentialResolver
    record = SimpleNamespace(status='active', provider='boltz_platform', encrypted_secret=b'encrypted')
    calls = []
    @contextmanager
    def sessions():
        yield 'db'
    class Service:
        def get_owned(self, db, credential_id, owner):
            calls.append((db, credential_id, owner))
            return record
        def decrypt(self, encrypted):
            assert encrypted == b'encrypted'
            return 'private-token'
    resolver = ProviderCredentialResolver(sessions, Service(),
                                         {('alice', 'provider-token'): ('record-id', 'boltz_platform')})
    assert resolver('alice', 'provider-token').get_secret_value() == 'private-token'
    assert calls == [('db', 'record-id', 'alice')]
    with pytest.raises(PermissionError):
        resolver('bob', 'provider-token')
    for status, provider in [('invalid', 'boltz_platform'), ('active', 'other')]:
        record.status, record.provider = status, provider
        with pytest.raises(PermissionError):
            resolver('alice', 'provider-token')


def test_unit_budget_and_missing_service(runtime):
    package, _, ledger, _, action = runtime
    manifest = json.loads((package / 'manifest.json').read_text())
    manifest['operations'][0]['budget_units'] = 3
    manifest['operations'][0]['service'] = 'scientific-api'
    (package / 'manifest.json').write_text(json.dumps(manifest))
    registry = SkillRegistry()
    digest = registry.install(package)
    action = action.model_copy(update={'skill_digest': digest})
    executor = Executor(registry, ledger, grants={'alice': {KEY}})
    with pytest.raises(ExecutionError, match='service'):
        executor.execute(action, owner='alice')
    from sable_runtime.executor import ServiceBinding
    executor = Executor(registry, ledger, grants={'alice': {KEY}},
                        services={'scientific-api': ServiceBinding('https://example.test')})
    with pytest.raises(LedgerError, match='budget'):
        executor.execute(action, owner='alice')
    assert ledger.events('campaign', 'alice') == []
