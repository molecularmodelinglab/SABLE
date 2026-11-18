"""Typed dispatch with host-owned authorization, service bindings and credentials."""
from dataclasses import dataclass, field
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Callable, Mapping

from jsonschema import Draft202012Validator
from pydantic import SecretStr

from .contracts import Action
from .local_worker import run_worker


class ExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceBinding:
    endpoint: str
    credential: str | None = None


@dataclass(frozen=True)
class ExecutionContext:
    campaign_id: str
    action_id: str
    service: ServiceBinding | None = None
    secret: SecretStr | None = field(default=None, repr=False)
    results: Mapping = field(default_factory=dict, repr=False)
    run_worker: Callable | None = field(default=None, repr=False)
    submit_job: Callable | None = field(default=None, repr=False)
    read_artifact: Callable | None = field(default=None, repr=False)


def contains_secret(value, secret):
    if isinstance(value, str):
        return secret in value
    if isinstance(value, dict):
        return any(contains_secret(k, secret) or contains_secret(v, secret) for k, v in value.items())
    if isinstance(value, list):
        return any(contains_secret(v, secret) for v in value)
    return False


class Executor:
    def __init__(self, registry, ledger, *, grants: Mapping[str, frozenset],
                 services: Mapping[str, ServiceBinding] | None = None,
                 credentials: Callable[[str, str], SecretStr] | None = None, jobs=None, artifacts=None):
        self.registry = registry
        self.ledger = ledger
        self.grants = {owner: frozenset(allowed) for owner, allowed in grants.items()}
        self.services = MappingProxyType(dict(services or {}))
        self.credentials = credentials
        self.jobs = jobs
        self.artifacts = artifacts

    def execute(self, request: Action, *, owner: str):
        # owner and grants come from trusted application authentication, never tool input.
        action = Action.model_validate(request.model_dump())
        key = (action.skill_id, action.skill_version, action.operation_id)
        if key not in self.grants.get(owner, frozenset()):
            raise PermissionError('Operation is not authorized')
        skill = self.registry.get(action.skill_id, action.skill_version)
        if action.skill_digest != skill.digest:
            raise ExecutionError('Installed skill does not match the pinned digest')
        operation = next((o for o in skill.manifest.operations if o.id == action.operation_id), None)
        if operation is None:
            raise ExecutionError('Unknown operation')
        # Ensure JSON-only values, including rejection of non-finite measurements.
        try:
            canonical = json.dumps(action.model_dump(), sort_keys=True, allow_nan=False)
            Draft202012Validator(operation.input_schema).validate(action.inputs)
        except Exception:
            raise ExecutionError('Input does not satisfy the operation contract') from None
        with self.ledger.connect() as db:
            self.ledger.owned(db, action.campaign_id, owner)
        service = self.services.get(operation.service) if operation.service else None
        if operation.service and service is None:
            raise ExecutionError('Required service is unavailable')
        secret = None
        if operation.credential:
            try:
                secret = self.credentials(owner, operation.credential) if self.credentials else None
                if not isinstance(secret, SecretStr) or not secret.get_secret_value():
                    raise ValueError()
            except Exception:
                raise ExecutionError('Required credential is unavailable') from None
        # Inputs must be scientific arguments, not copies of resolved credentials.
        if secret and contains_secret(action.inputs, secret.get_secret_value()):
            raise ExecutionError('Credential material is not a valid operation input')
        fingerprint = sha256(canonical.encode()).hexdigest()
        cached = self.ledger.claim(action, owner, fingerprint, operation.budget_units,
                                   {'skill_id': action.skill_id, 'skill_version': action.skill_version,
                                    'skill_digest': skill.digest, 'operation_id': operation.id,
                                    'budget_units': operation.budget_units})
        if cached is not None:
            return cached
        try:
            # Resolve successful results from this owned campaign, never model-written measurements.
            with self.ledger.connect() as db:
                self.ledger.owned(db, action.campaign_id, owner)
                rows = db.execute('SELECT id,result FROM actions WHERE campaign=? AND status=?',
                                  (action.campaign_id, 'succeeded')).fetchall()
                results = {row['id']: json.loads(row['result']) for row in rows}
                if self.jobs:
                    for job in self.jobs.list(action.campaign_id, owner):
                        if job['status'] in {'succeeded', 'partial'}:
                            results[job['action']] = job['result']
            context = ExecutionContext(action.campaign_id, action.action_id, service, secret,
                                       MappingProxyType(results),
                                       lambda name, payload: run_worker(skill, name, payload),
                                       (lambda name, payload: self.jobs.enqueue(action, skill, name, payload, owner)) if self.jobs else None,
                                       (lambda artifact_id: self.artifacts.read(action.campaign_id, owner, artifact_id)) if self.artifacts else None)
            result = self.registry.handler(skill, operation)(action.inputs, context)
            # Convert to JSON values before validation, storage and caller exposure.
            result = json.loads(json.dumps(result, allow_nan=False))
            Draft202012Validator(operation.output_schema).validate(result)
            if secret and contains_secret(result, secret.get_secret_value()):
                raise ValueError('Credential appeared in output')
        except Exception:
            # Do not expose exception text: provider messages may contain secrets.
            self.ledger.finish(action, owner, 'failed', {'error': 'operation_failed'})
            raise ExecutionError('Operation failed or returned an invalid result') from None
        self.ledger.finish(action, owner, 'succeeded', result)
        return result
