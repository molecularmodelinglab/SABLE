"""Explicit installation and progressive discovery of trusted packages.

Installing a package trusts its Python code. This registry is not a sandbox.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from .contracts import Manifest


@dataclass(frozen=True)
class InstalledSkill:
    manifest_json: str
    instructions: str
    code: bytes
    digest: str

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
        for name in ('manifest.json', 'SKILL.md', 'operations.py'):
            path = directory / name
            if path.is_symlink() or not path.is_file():
                raise ValueError('Required package files must be regular files')
            content[name] = path.read_bytes()
        manifest = Manifest.model_validate_json(content['manifest.json'])
        for operation in manifest.operations:
            validate_schema(operation.input_schema)
            validate_schema(operation.output_schema)
        key = (manifest.id, manifest.version)
        if key in self._skills:
            raise ValueError('Skill version already installed')
        digest = sha256()
        for name, data in sorted(content.items()):
            digest.update(name.encode() + b'\0' + str(len(data)).encode() + b'\0' + data)
        installed = InstalledSkill(manifest.model_dump_json(), content['SKILL.md'].decode(),
                                   content['operations.py'], digest.hexdigest())
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
