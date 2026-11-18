"""Explicit installation and progressive discovery of trusted packages.

Installing a package trusts its Python code. This registry is not a sandbox.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from importlib.util import find_spec

from jsonschema import Draft202012Validator
from .contracts import Manifest


@dataclass(frozen=True)
class InstalledSkill:
    manifest_json: str
    instructions: str
    code: bytes
    digest: str
    files: Mapping[str, bytes]

    @property
    def manifest(self):
        return Manifest.model_validate_json(self.manifest_json)


def validate_schema(schema):
    Draft202012Validator.check_schema(schema)
    # Keep contracts self-contained and prevent implicit remote reference fetching.
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('$ref', '$dynamicRef') and not str(item).startswith('#'):
                    raise ValueError('Schemas must use local references only')
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(schema)
    if schema.get('type') != 'object' or schema.get('additionalProperties') is not False:
        raise ValueError('Operation schemas must be closed objects')


class SkillRegistry:
    def __init__(self):
        self._skills = {}

    def install(self, directory: Path):
        directory = Path(directory).resolve()
        content = {}
        for name in ('manifest.json', 'SKILL.md'):
            path = directory / name
            if path.is_symlink() or not path.is_file():
                raise ValueError('Required package files must be regular files')
            content[name] = path.read_bytes()
        manifest = Manifest.model_validate_json(content['manifest.json'])
        entries = list(manifest.workers.values()) + list(manifest.remote_jobs.values())
        if manifest.completion_check:
            entries.append(manifest.completion_check)
        if manifest.requirements.probe:
            entries.append(manifest.requirements.probe)
        for entry in entries:
            path = directory / entry.file
            if path.is_symlink() or not path.is_file():
                raise ValueError('Entrypoint files must be regular package files')
            content[entry.file] = path.read_bytes()
        if manifest.operations:
            path = directory / 'operations.py'
            if path.is_symlink() or not path.is_file():
                raise ValueError('Executable skills require a regular operations.py file')
            content['operations.py'] = path.read_bytes()
        for operation in manifest.operations:
            validate_schema(operation.input_schema)
            validate_schema(operation.output_schema)
        for job in manifest.remote_jobs.values():
            validate_schema(job.output_schema)
        key = (manifest.id, manifest.version)
        if key in self._skills:
            raise ValueError('Skill version already installed')
        digest = sha256()
        for name, data in sorted(content.items()):
            digest.update(name.encode() + b'\0' + str(len(data)).encode() + b'\0' + data)
        installed = InstalledSkill(manifest.model_dump_json(), content['SKILL.md'].decode(),
                                   content.get('operations.py', b''), digest.hexdigest(), MappingProxyType(content))
        self._skills[key] = installed
        return installed.digest

    def get(self, skill_id, version):
        try:
            return self._skills[(skill_id, version)]
        except KeyError:
            raise ValueError('Skill version is not installed') from None

    def discover(self, allowed):
        return [dict(id=s.manifest.id, version=s.manifest.version,
                     description=s.manifest.description, digest=s.digest)
                for key, s in sorted(self._skills.items()) if key in allowed]

    def describe(self, skill_id, version, allowed):
        if (skill_id, version) not in allowed:
            raise PermissionError('Skill is not authorized')
        skill = self.get(skill_id, version)
        return dict(manifest=json.loads(skill.manifest_json), instructions=skill.instructions,
                    digest=skill.digest)

    def handler(self, skill, operation):
        # Code is loaded from the installation snapshot, never a mutable source path.
        namespace = {'__name__': f'sable_skill_{skill.digest}'}
        exec(compile(skill.code, f'<skill:{skill.manifest.id}@{skill.manifest.version}>', 'exec'), namespace)
        handler = namespace.get(operation.handler)
        if not callable(handler):
            raise ValueError('Installed operation handler is missing')
        return handler

    def available(self, services=()):
        """Evaluate requirements of host-installed trusted packages, then dependencies."""
        candidates = {}
        for key, skill in self._skills.items():
            requirements = skill.manifest.requirements
            try:
                required_services = set(requirements.services) | {j.service for j in skill.manifest.remote_jobs.values()}
                if not required_services <= set(services):
                    continue
                allowed_env = set(os.getenv('SABLE_V2_WORKER_ENV_ALLOWLIST', '').split(','))
                if any(set(worker.environment) - allowed_env for worker in skill.manifest.workers.values()):
                    continue
                if not all(find_spec(name) is not None for name in requirements.modules):
                    continue
                if requirements.probe:
                    entry = requirements.probe
                    namespace = {'__name__': f'sable_probe_{skill.digest}'}
                    exec(compile(skill.files[entry.file], f'<skill:{entry.file}>', 'exec'), namespace)
                    if namespace[entry.handler]() is not True:
                        continue
            except Exception:
                # Missing optional dependencies/configuration must not break discovery.
                continue
            candidates[key] = skill
        available = set()
        while True:
            names = {key[0] for key in available}
            added = {key for key, skill in candidates.items()
                     if set(skill.manifest.requirements.skills) <= names
                     and (not skill.manifest.requirements.any_skills
                          or set(skill.manifest.requirements.any_skills) & names)} - available
            if not added:
                return available
            available.update(added)

    def install_directory(self, root, services=()):
        """The deployment-owned directory is the installation/trust boundary."""
        for directory in sorted(Path(root).iterdir()):
            if directory.is_dir() and not directory.is_symlink() and (directory / 'manifest.json').is_file():
                self.install(directory)
        return self.available(services)

    def remote_handler(self, skill, job, phase):
        handler_name = getattr(job, phase)
        if handler_name is None:
            raise ValueError('Remote operation is unsupported')
        namespace = {'__name__': f'sable_remote_{skill.digest}'}
        exec(compile(skill.files[job.file], f'<skill:{job.file}>', 'exec'), namespace)
        handler = namespace.get(handler_name)
        if not callable(handler):
            raise ValueError('Remote handler is missing')
        return handler

    def completion_feedback(self, skill, results, evidence):
        entry = skill.manifest.completion_check
        if entry is None:
            return None
        namespace = {'__name__': f'sable_completion_{skill.digest}'}
        exec(compile(skill.files[entry.file], f'<skill:{entry.file}>', 'exec'), namespace)
        return namespace[entry.handler](results, evidence)
