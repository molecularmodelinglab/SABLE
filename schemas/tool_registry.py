"""Contracts for registering and tracking workflow tool implementations."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class ToolKind(str, Enum):
    """Workflow stages that can have swappable implementations."""

    ARGUMENT_EXTRACTOR = "argument_extractor"
    ENUMERATOR = "enumerator"
    OPTIMIZER = "optimizer"
    CHARACTERIZER = "characterizer"
    SUMMARIZER = "summarizer"
    SETUP = "setup"
    EXIT_CHECKER = "exit_checker"


class ToolCapability(BaseModel):
    """Describes what a tool can provide and what context it needs."""

    provides: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    accepts: list[str] = Field(
        default_factory=list,
        description="Optional input modes or strategies accepted by the tool.",
    )
    batch: bool = True
    local: bool = True
    cost: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolSpec(BaseModel):
    """Declarative registry entry for a concrete tool implementation."""

    id: str
    kind: ToolKind
    class_path: str = Field(
        ...,
        description="Import path in 'module:attribute' form.",
    )
    version: str = "1"
    enabled: bool = True
    priority: int = Field(
        default=100,
        description="Lower values are preferred when multiple tools match.",
    )
    capability: ToolCapability = Field(default_factory=ToolCapability)
    config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _normalize_id(cls, value: str) -> str:
        return value.strip().lower().replace(" ", "_")

    @field_validator("class_path")
    @classmethod
    def _validate_class_path(cls, value: str) -> str:
        if ":" not in value:
            raise ValueError("class_path must use 'module:attribute' form")
        return value


class ToolSelection(BaseModel):
    """A selected tool for a workflow stage."""

    stage: ToolKind | str
    tool_id: str
    reason: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolRunRecord(BaseModel):
    """Audit-friendly record of a single tool execution."""

    tool_id: str
    stage: ToolKind | str
    status: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
