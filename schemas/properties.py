"""Property catalog contracts for workflow targets and tool capabilities.

The workflow currently treats property names as strings in several places.
These models provide a shared vocabulary for later moving aliases, bounds,
parser hints, and tool capability matching out of individual nodes.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Tuple

from pydantic import BaseModel, Field, field_validator


class PropertySpec(BaseModel):
    """Canonical metadata for a molecular or experimental property."""

    id: str = Field(..., description="Canonical property identifier, e.g. 'qed'.")
    label: str = Field(..., description="Human-readable display label.")
    aliases: list[str] = Field(
        default_factory=list,
        description="Alternate names accepted from prompts, tools, or APIs.",
    )
    parser_keywords: list[str] = Field(
        default_factory=list,
        description="Prompt keywords that indicate this property is requested.",
    )
    default_bounds: Optional[Tuple[float, float]] = Field(
        default=None,
        description="Default valid or useful bounds for target construction.",
    )
    default_mode: str = Field(
        default="MAX",
        description="Default optimization mode: MAX, MIN, or MATCH.",
    )
    default_transformation: Optional[str] = Field(
        default="LINEAR",
        description="Default transformation used by optimizers when applicable.",
    )
    tool_names: dict[str, Optional[str]] = Field(
        default_factory=dict,
        description="Tool-specific output names for this property.",
    )
    units: Optional[str] = Field(default=None, description="Optional property units.")
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _normalize_id(cls, value: str) -> str:
        return normalize_property_name(value)

    @field_validator("aliases", "parser_keywords")
    @classmethod
    def _normalize_terms(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for value in values:
            term = value.strip()
            if not term:
                continue
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(term)
        return normalized


class PropertyCatalog(BaseModel):
    """Collection of property specs with normalization and lookup helpers."""

    properties: dict[str, PropertySpec] = Field(default_factory=dict)

    @classmethod
    def from_specs(cls, specs: Iterable[PropertySpec]) -> "PropertyCatalog":
        return cls(properties={spec.id: spec for spec in specs})

    def normalize(self, value: str) -> str:
        """Return the canonical property id for a value or alias."""

        normalized = normalize_property_name(value)
        if normalized in self.properties:
            return normalized

        for spec in self.properties.values():
            aliases = {normalize_property_name(alias) for alias in spec.aliases}
            aliases.update(normalize_property_name(keyword) for keyword in spec.parser_keywords)
            if normalized in aliases:
                return spec.id

        return normalized

    def get(self, value: str) -> Optional[PropertySpec]:
        return self.properties.get(self.normalize(value))

    def bounds_for(self, value: str) -> Optional[Tuple[float, float]]:
        spec = self.get(value)
        return spec.default_bounds if spec else None

    def keywords_for(self, value: str) -> list[str]:
        spec = self.get(value)
        return list(spec.parser_keywords) if spec else []

    def parser_keyword_map(self) -> dict[str, list[str]]:
        """Return prompt parser keywords keyed by canonical property id."""

        return {
            prop_id: list(spec.parser_keywords)
            for prop_id, spec in self.properties.items()
            if spec.parser_keywords
        }

    def alias_map(self) -> dict[str, str]:
        """Return normalized alias -> canonical property id mappings."""

        aliases: dict[str, str] = {}
        for spec in self.properties.values():
            for alias in spec.aliases:
                normalized_alias = normalize_property_name(alias)
                if normalized_alias != spec.id:
                    aliases[normalized_alias] = spec.id
        return aliases

    def tool_properties(self, tool_id: str) -> set[str]:
        """Return normalized properties and output names available from a tool."""

        tool_key = tool_id.lower()
        available: set[str] = set()
        for spec in self.properties.values():
            if tool_key not in spec.tool_names:
                continue
            output_name = spec.tool_names.get(tool_key)
            if output_name:
                available.add(spec.id)
                available.add(normalize_property_name(output_name))
        return available

    def tool_property_mappings(
        self,
        primary_tool: str = "rdkit",
        secondary_tool: str = "stoplight",
    ) -> dict[str, tuple[Optional[str], Optional[str]]]:
        """Return compatibility mappings of property id/alias to tool output names."""

        mappings: dict[str, tuple[Optional[str], Optional[str]]] = {}
        for spec in self.properties.values():
            value = (
                spec.tool_names.get(primary_tool.lower()),
                spec.tool_names.get(secondary_tool.lower()),
            )
            mappings[spec.id] = value
            for alias in spec.aliases:
                mappings.setdefault(normalize_property_name(alias), value)
        return mappings


def load_property_catalog(path: str | Path | None = None) -> PropertyCatalog:
    """Load a property catalog from YAML."""

    import yaml

    catalog_path = Path(path) if path else Path(__file__).resolve().parents[1] / "config" / "properties.yml"
    with catalog_path.open() as handle:
        raw = yaml.safe_load(handle) or {}

    raw_properties = raw.get("properties", {})
    specs: list[PropertySpec] = []
    for prop_id, payload in raw_properties.items():
        data = dict(payload or {})
        data.setdefault("id", prop_id)
        data.setdefault("label", prop_id)
        specs.append(PropertySpec(**data))

    return PropertyCatalog.from_specs(specs)


@lru_cache(maxsize=1)
def get_property_catalog() -> PropertyCatalog:
    """Return the default project property catalog."""

    return load_property_catalog()


def normalize_property_name(value: str) -> str:
    """Normalize a property name into the canonical identifier shape."""

    return value.strip().lower().replace(" ", "_").replace("-", "_")
