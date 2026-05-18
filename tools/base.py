"""Base interfaces for modular LIZARD workflow tools.

These contracts are intentionally small. Existing tools can be adapted to them
incrementally while current LangChain BaseTool implementations remain in place.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, Field

from schemas.tool_registry import ToolKind, ToolSpec
from schemas.tool_schemas import (
    ArgumentExtractionRequest,
    ArgumentExtractionResult,
    BORecommendationRequest,
    BORecommendationResult,
    CharacterizationRequest,
    CharacterizationResult,
    EnumerationRequest,
    EnumerationResult,
    SummaryRequest,
    SummaryResult,
)


RequestT = TypeVar("RequestT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)


class ToolExecutionContext(BaseModel):
    """Context passed to modular tools without requiring full state coupling."""

    workflow_id: str | None = None
    run_paths: dict[str, str] = Field(default_factory=dict)
    stage_config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LizardTool(ABC, Generic[RequestT, ResultT]):
    """Abstract base class for future registry-managed tool adapters."""

    spec: ToolSpec

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def kind(self) -> ToolKind:
        return self.spec.kind

    @abstractmethod
    def run(self, request: RequestT, context: ToolExecutionContext | None = None) -> ResultT:
        """Execute the tool with a typed request and return a typed result."""


@runtime_checkable
class SupportsEnumeration(Protocol):
    def run(
        self,
        request: EnumerationRequest,
        context: ToolExecutionContext | None = None,
    ) -> EnumerationResult:
        ...


@runtime_checkable
class SupportsOptimization(Protocol):
    def run(
        self,
        request: BORecommendationRequest,
        context: ToolExecutionContext | None = None,
    ) -> BORecommendationResult:
        ...


@runtime_checkable
class SupportsCharacterization(Protocol):
    def run(
        self,
        request: CharacterizationRequest,
        context: ToolExecutionContext | None = None,
    ) -> CharacterizationResult:
        ...


@runtime_checkable
class SupportsArgumentExtraction(Protocol):
    def run(
        self,
        request: ArgumentExtractionRequest,
        context: ToolExecutionContext | None = None,
    ) -> ArgumentExtractionResult:
        ...


@runtime_checkable
class SupportsSummarization(Protocol):
    def run(
        self,
        request: SummaryRequest,
        context: ToolExecutionContext | None = None,
    ) -> SummaryResult:
        ...
