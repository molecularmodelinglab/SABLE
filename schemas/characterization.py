from enum import Enum
from typing import Any, Dict, List
from pydantic import BaseModel, Field

from schemas.properties import get_property_catalog
from schemas.properties import normalize_property_name as _basic_normalize_property_name
from schemas.tool_registry import ToolKind
from tools.registry import ToolRegistry, get_tool_registry



class PropertySource(str, Enum):
    """Enumeration for the source of a calculated property value."""
    RDKIT = "rdkit"
    STOPLIGHT = "stoplight"
    LLM = "llm"
    EXPERIMENTAL = "experimental"

PROPERTY_CATALOG = get_property_catalog()

# Compatibility constants derived from config/properties.yml.
RDKIT_PROPERTIES = PROPERTY_CATALOG.tool_properties("rdkit")
STOPLIGHT_PROPERTIES = PROPERTY_CATALOG.tool_properties("stoplight")
BOLTZ_PROPERTIES = PROPERTY_CATALOG.tool_properties("boltz") | {"affinity"}
PROPERTY_ALIASES = PROPERTY_CATALOG.alias_map()
PROPERTY_MAPPINGS = PROPERTY_CATALOG.tool_property_mappings("rdkit", "stoplight")



class CharacterizationRequest(BaseModel):
    """A request to characterize one or more molecules."""
    molecule_ids: List[str]
    properties: List[str]
    tool_preference: str = "auto"
    include_all_available: bool = Field(
        default=False,
        description="If True, calculate all properties available from the selected tool.",
    )


class CharacterizationResult(BaseModel):
    """The characterization result for a single molecule."""
    molecule_id: str
    smiles: str
    properties: Dict[str, float]
    sources: Dict[str, PropertySource] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


class CharacterizationBatch(BaseModel):
    """A batch of characterization results from a single tool run."""
    results: List[CharacterizationResult]
    tool_used: str
    total_molecules: int
    successful: int
    failed: int
    metadata: Dict[str, Any] = Field(default_factory=dict)


def select_characterization_tool_ids(
    properties: List[str],
    registry: ToolRegistry | None = None,
    context: Any | None = None,
) -> List[str]:
    """Select characterizer tool IDs using registry capabilities."""

    registry = registry or get_tool_registry()
    normalized_props = {normalize_property_name(p) for p in properties}

    requires_boltz = any(prop in BOLTZ_PROPERTIES for prop in normalized_props)
    remaining_props = {prop for prop in normalized_props if prop not in BOLTZ_PROPERTIES}
    selected: List[str] = []

    if remaining_props:
        single_tool = registry.select(
            kind=ToolKind.CHARACTERIZER,
            provides=remaining_props,
            context=context,
        )
        non_boltz_single = [spec for spec in single_tool if spec.id != "boltz"]
        if non_boltz_single:
            selected.append(non_boltz_single[0].id)
        else:
            for tool_id in ("rdkit", "stoplight"):
                spec = registry.get(tool_id)
                coverage = {PROPERTY_CATALOG.normalize(prop) for prop in spec.provides}
                if remaining_props & coverage:
                    selected.append(tool_id)

    if requires_boltz:
        selected.append("boltz")

    deduped: List[str] = []
    for tool_id in selected:
        if tool_id not in deduped:
            deduped.append(tool_id)
    return deduped


def determine_best_tool(properties: List[str]) -> str:
    """Compatibility wrapper returning the legacy single-tool label."""

    tool_ids = select_characterization_tool_ids(properties)
    non_boltz = [tool_id for tool_id in tool_ids if tool_id != "boltz"]
    if not non_boltz and "boltz" in tool_ids:
        return "boltz"
    if len(non_boltz) == 1 and len(tool_ids) == 1:
        return non_boltz[0]
    if len(non_boltz) == 1 and "boltz" in tool_ids:
        return "combined"
    return "combined"


def normalize_property_name(prop: str) -> str:
    """
    Converts a property name into a standardized format.

    Example: "Polar Surface Area" -> "polar_surface_area"

    Args:
        prop: The property name to normalize.

    Returns:
        The normalized property name string.
    """
    normalized = _basic_normalize_property_name(prop)
    return PROPERTY_CATALOG.normalize(PROPERTY_ALIASES.get(normalized, normalized))
