"""Exercise extension packages, execution boundaries, and deployment discovery."""
import json

import pytest
from pydantic import ValidationError

from sable_runtime.contracts import Action, Budget, LocalWorker
from sable_runtime.executor import Executor
from sable_runtime.ledger import Ledger
from sable_runtime.local_worker import run_worker, run_snapshot
from sable_runtime.registry import SkillRegistry


def package(root, name='example'):
    folder = root / name
    folder.mkdir()
    schema = {'type': 'object', 'additionalProperties': False,
              'properties': {'value': {'type': 'integer'}}, 'required': ['value']}
    manifest = {'id': name, 'version': '1.0.0', 'description': 'Example extension',
                'workers': {'compute': {'file': 'worker.py', 'handler': 'run'}},
                'operations': [{'id': 'calculate', 'description': 'Calculate a value', 'handler': 'calculate',
                                'input_schema': schema, 'output_schema': schema}]}
    (folder / 'manifest.json').write_text(json.dumps(manifest))
    (folder / 'SKILL.md').write_text('Example extension instructions')
    (folder / 'operations.py').write_text("def calculate(inputs, context):\n    return context.run_worker('compute', inputs)\n")
    (folder / 'worker.py').write_text("def run(payload):\n    print('library logging')\n    return {'value': payload['value'] * 2}\n")
    return folder


def test_new_package_executes_without_core_dispatch_and_uses_snapshot(tmp_path):
    folder = package(tmp_path)
    registry = SkillRegistry()
    allowed = registry.install_directory(tmp_path)
    assert allowed == {('example', '1.0.0')}
    skill = registry.get('example', '1.0.0')
    # Changes on disk do not alter execution of an already installed snapshot.
    (folder / 'worker.py').write_text("def run(payload):\n    return {'value': 999}\n")
    ledger = Ledger(tmp_path / 'ledger.sqlite')
    ledger.create_campaign('campaign', 'alice', Budget(max_actions=2, max_units=2))
    executor = Executor(registry, ledger, grants={'alice': {('example', '1.0.0', 'calculate')}})
    action = Action(campaign_id='campaign', action_id='one', skill_id='example', skill_version='1.0.0',
                    skill_digest=skill.digest, operation_id='calculate', inputs={'value': 3})
    assert executor.execute(action, owner='alice') == {'value': 6}
    other = SkillRegistry(); other.install(folder)
    assert other.get('example', '1.0.0').digest != skill.digest
    with pytest.raises(ValueError, match='not declared'):
        run_worker(skill, 'arbitrary', {})


def test_worker_timeout_errors_and_environment(tmp_path, monkeypatch):
    entry = LocalWorker(file='worker.py', handler='run', timeout_seconds=1)
    with pytest.raises(RuntimeError, match='time budget'):
        run_snapshot(entry, b'import time\ndef run(payload):\n time.sleep(5)\n', {})
    with pytest.raises(RuntimeError, match='failed'):
        run_snapshot(entry, b'def run(payload):\n raise ValueError("private details")\n', {})
    monkeypatch.setenv('OPENAI_API_KEY', 'private-test-value')
    monkeypatch.setenv('EXAMPLE_DATA', 'fixture')
    monkeypatch.setenv('SABLE_V2_WORKER_ENV_ALLOWLIST', 'EXAMPLE_DATA')
    entry = entry.model_copy(update={'environment': ['EXAMPLE_DATA']})
    assert run_snapshot(entry, b'import os\ndef run(payload):\n return {"data": os.getenv("EXAMPLE_DATA"), "secret": os.getenv("OPENAI_API_KEY")}\n', {}) == {'data': 'fixture', 'secret': None}
    with pytest.raises(ValueError, match='not authorized'):
        run_snapshot(entry.model_copy(update={'environment': ['OPENAI_API_KEY']}), b'', {})


def test_entrypoint_paths_and_files_rejected(tmp_path):
    for filename in ('../worker.py', '/tmp/worker.py', 'sub/worker.py'):
        with pytest.raises(ValidationError):
            LocalWorker(file=filename, handler='run')
    folder = package(tmp_path)
    (folder / 'worker.py').unlink()
    with pytest.raises(ValueError, match='regular'):
        SkillRegistry().install(folder)
    (folder / 'worker.py').symlink_to(folder / 'operations.py')
    with pytest.raises(ValueError, match='regular'):
        SkillRegistry().install(folder)


def test_dependency_probes_and_cycles(tmp_path):
    for name, requirements in [('available', {}), ('missing', {'modules': ['nonexistent_example_module']}),
                               ('dependent', {'skills': ['available']}), ('cycle', {'skills': ['cycle']}),
                               ('disabled', {'probe': {'file': 'availability.py', 'handler': 'available'}})]:
        folder = package(tmp_path, name)
        manifest = json.loads((folder / 'manifest.json').read_text())
        manifest['requirements'] = requirements
        (folder / 'manifest.json').write_text(json.dumps(manifest))
        (folder / 'availability.py').write_text('def available():\n    return False\n')
    registry = SkillRegistry()
    assert registry.install_directory(tmp_path) == {('available', '1.0.0'), ('dependent', '1.0.0')}


def test_worker_environment_controls_availability(tmp_path, monkeypatch):
    folder = package(tmp_path)
    manifest = json.loads((folder / 'manifest.json').read_text())
    manifest['workers']['compute']['environment'] = ['EXAMPLE_DATA']
    (folder / 'manifest.json').write_text(json.dumps(manifest))
    registry = SkillRegistry(); registry.install(folder)
    monkeypatch.delenv('SABLE_V2_WORKER_ENV_ALLOWLIST', raising=False)
    assert registry.available() == set()
    monkeypatch.setenv('SABLE_V2_WORKER_ENV_ALLOWLIST', 'EXAMPLE_DATA')
    assert registry.available() == {('example', '1.0.0')}
