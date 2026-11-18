"""Versioned, closed contracts for installed skills and execution requests."""
from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r'^[a-z][a-z0-9_-]{0,63}$')]


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class Operation(Contract):
    id: Identifier
    description: str = Field(min_length=1, max_length=1000)
    handler: str = Field(pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$')
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    budget_units: int = Field(default=1, ge=1, strict=True)
    service: Identifier | None = None
    credential: Identifier | None = None


class PythonEntryPoint(Contract):
    file: str = Field(pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*\.py$')
    handler: str = Field(pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$')


class LocalWorker(PythonEntryPoint):
    timeout_seconds: int = Field(default=120, ge=1, le=120, strict=True)
    environment: list[str] = Field(default_factory=list)


class Requirements(Contract):
    modules: list[str] = Field(default_factory=list)
    skills: list[Identifier] = Field(default_factory=list)
    any_skills: list[Identifier] = Field(default_factory=list)
    services: list[Identifier] = Field(default_factory=list)
    probe: PythonEntryPoint | None = None


class RemoteJob(Contract):
    file: str = Field(pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*\.py$')
    submit: str = Field(pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$')
    poll: str = Field(pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$')
    cancel: str | None = Field(default=None, pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$')
    service: Identifier
    credential: Identifier | None = None
    reconcilable: bool = True
    timeout_seconds: int = Field(default=900, ge=1, le=604800, strict=True)
    poll_interval_seconds: int = Field(default=5, ge=1, le=3600, strict=True)
    output_schema: dict[str, Any]


class Manifest(Contract):
    schema_version: Literal[1] = 1
    id: Identifier
    version: str = Field(pattern=r'^\d+\.\d+\.\d+$')
    description: str = Field(min_length=1, max_length=1000)
    runtime: Literal['python'] = 'python'
    operations: list[Operation] = Field(default_factory=list)
    workers: dict[Identifier, LocalWorker] = Field(default_factory=dict)
    remote_jobs: dict[Identifier, RemoteJob] = Field(default_factory=dict)
    requirements: Requirements = Field(default_factory=Requirements)
    completion_check: PythonEntryPoint | None = None

    @model_validator(mode='after')
    def unique_operations(self):
        ids = [operation.id for operation in self.operations]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate operation IDs')
        return self


class Action(Contract):
    campaign_id: Identifier
    action_id: Identifier
    skill_id: Identifier
    skill_version: str
    skill_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    operation_id: Identifier
    inputs: dict[str, Any]


class Budget(Contract):
    max_actions: int = Field(ge=1, strict=True)
    max_units: int = Field(ge=1, strict=True)
