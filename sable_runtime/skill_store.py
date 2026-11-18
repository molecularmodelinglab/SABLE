"""Immutable skill packages with atomic activation and revocation records."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
from .skill_validation import validate_package
from .registry import SkillRegistry


class SkillStore:
    def __init__(self, root):
        self.root = Path(root)

    def records(self):
        path = self.root / 'index.json'
        if not path.exists():
            return []
        data = json.loads(path.read_text())
        if data.get('schema_version') != 1 or not isinstance(data.get('packages'), list):
            raise ValueError('Unsupported skill store index')
        records = data['packages']
        seen, active = set(), set()
        for record in records:
            if set(record) != {'id', 'version', 'digest', 'active', 'revoked'}:
                raise ValueError('Invalid skill store record')
            from .contracts import Manifest
            Manifest(id=record['id'], version=record['version'], description='Stored package')
            if type(record['active']) is not bool or type(record['revoked']) is not bool:
                raise ValueError('Invalid activation state')
            if not isinstance(record['digest'], str) or len(record['digest']) != 64:
                raise ValueError('Invalid package digest')
            key = (record['id'], record['version'])
            if key in seen or (record['active'] and (record['id'] in active or record['revoked'])):
                raise ValueError('Conflicting skill store records')
            seen.add(key)
            if record['active']:
                active.add(record['id'])
        return records

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _save(self, records):
        fd, temporary = tempfile.mkstemp(prefix='.index-', dir=self.root)
        try:
            with os.fdopen(fd, 'w') as out:
                json.dump({'schema_version': 1, 'packages': records}, out, indent=2)
                out.flush(); os.fsync(out.fileno())
            os.replace(temporary, self.root / 'index.json')
            fd = os.open(self.root, os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def install(self, directory, activate=False):
        skill = validate_package(directory)
        manifest = skill.manifest
        with self.locked():
            records = self.records()
            existing = next((r for r in records if (r['id'],r['version']) == (manifest.id,manifest.version)), None)
            if existing and existing['digest'] != skill.digest:
                raise ValueError('A published version is immutable; increment the version')
            if existing and existing['revoked']:
                raise ValueError('A revoked version cannot be reinstalled or activated')
            target = self.root / 'packages' / manifest.id / manifest.version
            if any(part.is_symlink() for part in (self.root / 'packages', target.parent, target)):
                raise ValueError('Package storage paths must not be symlinks')
            if existing and validate_package(target).digest != existing['digest']:
                raise ValueError('Installed package differs from its recorded digest')
            if not existing:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    # An interrupted install may leave an unindexed but complete package.
                    check = validate_package(target)
                    if check.digest != skill.digest:
                        raise ValueError('Unindexed package differs from requested package')
                else:
                    with tempfile.TemporaryDirectory(prefix='.package-', dir=target.parent) as temp:
                        snapshot = Path(temp) / 'snapshot'; snapshot.mkdir()
                        for name, content in skill.files.items():
                            with (snapshot / name).open('wb') as file:
                                file.write(content); file.flush(); os.fsync(file.fileno())
                        os.replace(snapshot, target)
                existing = {'id':manifest.id,'version':manifest.version,'digest':skill.digest,'active':False,'revoked':False}
                records.append(existing)
            if activate:
                for row in records:
                    if row['id'] == manifest.id:
                        row['active'] = row is existing
            self._save(records)
        return dict(existing)

    def set_state(self, skill_id, version, state):
        with self.locked():
            records = self.records()
            target = next((r for r in records if (r['id'],r['version']) == (skill_id,version)), None)
            if target is None:
                raise ValueError('Skill version is not installed')
            if state not in {'activate','disable','revoke'}:
                raise ValueError('Unknown package state')
            if target['revoked'] and state != 'revoke':
                raise ValueError('Revocation is permanent; publish a new version')
            if state == 'activate':
                path = self.root / 'packages' / skill_id / version
                if any(part.is_symlink() for part in (self.root / 'packages',path.parent,path)):
                    raise ValueError('Package storage paths must not be symlinks')
                if validate_package(path).digest != target['digest']:
                    raise ValueError('Installed package differs from its recorded digest')
                for row in records:
                    if row['id'] == skill_id:
                        row['active'] = row is target
            else:
                target['active'] = False
                if state == 'revoke':
                    target['revoked'] = True
            self._save(records)
            return dict(target)

    def load(self, services=()):
        registry = SkillRegistry()
        records = self.records()
        for row in records:
            if row['revoked']:
                continue
            path = self.root / 'packages' / row['id'] / row['version']
            if any(part.is_symlink() for part in (self.root / 'packages', path.parent, path)):
                raise ValueError('Installed package directories must not be symlinks')
            if registry.install(path) != row['digest']:
                raise ValueError('Installed package content differs from its recorded digest')
        eligible = {(r['id'],r['version']) for r in records if not r['revoked']}
        allowed = registry.available(services, candidates=eligible)
        selected = registry.available(services, candidates={(r['id'],r['version']) for r in records if r['active']})
        return registry, allowed, selected


def load_deployment_skills(bundled, services=()):
    root = os.getenv('SABLE_SKILL_STORE_PATH')
    if root:
        if not Path(root).is_absolute():
            raise ValueError('Skill store requires an absolute deployment path')
        return SkillStore(root).load(services)
    registry = SkillRegistry()
    allowed = registry.install_directory(bundled, services)
    return registry, allowed, allowed


def deployment_revision():
    """Track store changes so activation and revocation reach running services."""
    from hashlib import sha256
    root = os.getenv('SABLE_SKILL_STORE_PATH')
    if not root:
        return 'bundled'
    index = Path(root) / 'index.json'
    return root + ':' + (sha256(index.read_bytes()).hexdigest() if index.exists() else 'empty')
