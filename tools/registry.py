"""Registry for declarative SABLE workflow tools.

Phase 3 introduces this registry without changing node execution. Later phases
can use it to select and instantiate stage implementations.
"""

from __future__ import annotations

import importlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

from schemas.properties import get_property_catalog
from schemas.tool_registry import ToolKind, ToolSelection, ToolSpec


class ToolRegistryError(Exception):
    """Raised when the tool registry cannot load or resolve a tool."""


class ToolRegistry:
    """In-memory registry of declarative tool specs."""

    def __init__(self, specs: Iterable[ToolSpec] = ()) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if spec.id in self._specs:
            raise ToolRegistryError(f"Duplicate tool id: {spec.id}")
        self._specs[spec.id] = spec

    def get(self, tool_id: str) -> ToolSpec:
        normalized = tool_id.strip().lower().replace(" ", "_")
        try:
            return self._specs[normalized]
        except KeyError as exc:
            raise ToolRegistryError(f"Unknown tool id: {tool_id}") from exc

    def list(
        self,
        kind: ToolKind | str | None = None,
        enabled_only: bool = True,
    ) -> list[ToolSpec]:
        specs = list(self._specs.values())
        if kind is not None:
            kind_value = kind.value if isinstance(kind, ToolKind) else str(kind)
            specs = [spec for spec in specs if spec.kind.value == kind_value]
        if enabled_only:
            specs = [spec for spec in specs if spec.enabled]
        return sorted(specs, key=lambda spec: (spec.priority, spec.id))

    def select(
        self,
        kind: ToolKind | str,
        provides: Optional[Iterable[str]] = None,
        accepts: Optional[Iterable[str]] = None,
        requires: Optional[Iterable[str]] = None,
        context: Any | None = None,
        enabled_only: bool = True,
    ) -> list[ToolSpec]:
        """Return matching specs ordered by priority."""

        _ = context  # Reserved for context-aware selectors in later phases.
        requested_provides = _normalize_terms(provides)
        requested_accepts = _normalize_terms(accepts, normalize=False)
        required_context = _normalize_terms(requires, normalize=False)

        matches: list[ToolSpec] = []
        for spec in self.list(kind=kind, enabled_only=enabled_only):
            spec_provides = _normalize_terms(spec.capability.provides)
            spec_accepts = _normalize_terms(spec.capability.accepts, normalize=False)
            spec_requires = _normalize_terms(spec.capability.requires, normalize=False)

            if requested_provides and not requested_provides <= spec_provides:
                continue
            if requested_accepts and not requested_accepts <= spec_accepts:
                continue
            if required_context and not spec_requires <= required_context:
                continue
            matches.append(spec)

        return matches

    def select_one(
        self,
        kind: ToolKind | str,
        provides: Optional[Iterable[str]] = None,
        accepts: Optional[Iterable[str]] = None,
        requires: Optional[Iterable[str]] = None,
        context: Any | None = None,
        enabled_only: bool = True,
    ) -> ToolSpec:
        matches = self.select(
            kind=kind,
            provides=provides,
            accepts=accepts,
            requires=requires,
            context=context,
            enabled_only=enabled_only,
        )
        if not matches:
            raise ToolRegistryError(
                f"No tool matched kind={kind!r}, provides={list(provides or [])!r}, "
                f"accepts={list(accepts or [])!r}"
            )
        return matches[0]

    def selection_for(self, stage: ToolKind | str, spec: ToolSpec, reason: str | None = None) -> ToolSelection:
        return ToolSelection(
            stage=stage,
            tool_id=spec.id,
            reason=reason,
            config=dict(spec.config),
            metadata={"class_path": spec.class_path, **spec.metadata},
        )

    def create(self, tool_id: str, **overrides: Any) -> Any:
        """Instantiate a registered tool using its class path and config."""

        spec = self.get(tool_id)
        target = import_from_path(spec.class_path)
        config = {**spec.config, **overrides}
        if isinstance(target, type):
            return target(**config)
        if config:
            raise ToolRegistryError(f"Cannot pass config to non-class registry target: {spec.class_path}")
        return target


def import_from_path(class_path: str) -> Any:
    """Import an object from 'module:attribute' path syntax."""

    module_name, sep, attr_name = class_path.partition(":")
    if not sep or not module_name or not attr_name:
        raise ToolRegistryError(f"Invalid class path: {class_path}")
    module = importlib.import_module(module_name)
    try:
        return getattr(module, attr_name)
    except AttributeError as exc:
        raise ToolRegistryError(f"Object {attr_name!r} not found in {module_name!r}") from exc


def load_tool_registry(path: str | Path | None = None) -> ToolRegistry:
    """Load tool specs from YAML and return a registry."""

    import yaml

    registry_path = Path(path) if path else Path(__file__).resolve().parents[1] / "config" / "tools.yml"
    with registry_path.open() as handle:
        raw = yaml.safe_load(handle) or {}

    specs = [ToolSpec(**payload) for payload in raw.get("tools", [])]
    return ToolRegistry(specs)


@lru_cache(maxsize=1)
def get_tool_registry() -> ToolRegistry:
    """Return the default project tool registry."""

    return load_tool_registry()


def _normalize_terms(values: Optional[Iterable[str]], normalize: bool = True) -> set[str]:
    if not values:
        return set()

    catalog = get_property_catalog()
    normalized: set[str] = set()
    for value in values:
        if normalize:
            normalized.add(catalog.normalize(value))
        else:
            normalized.add(str(value).strip())
    return normalized
