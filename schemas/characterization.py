from enum import Enum
from typing import Any, Dict, List
from pydantic import BaseModel, Field

from schemas.properties import get_property_catalog
from schemas.properties import normalize_property_name as _basic_normalize_property_name



class CharacterizationTool(str, Enum):
    """Enumeration for the available molecular characterization tools."""
    RDKIT = "rdkit"
    STOPLIGHT = "stoplight"
    BOLTZ = "boltz"
    COMBINED = "combined"    # Use both RDKit and Stoplight
    AUTO = "auto"          # Automatically decide the best tool


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
    tool_preference: CharacterizationTool = CharacterizationTool.AUTO
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
    tool_used: CharacterizationTool
    total_molecules: int
    successful: int
    failed: int
    metadata: Dict[str, Any] = Field(default_factory=dict)


def determine_best_tool(properties: List[str]) -> CharacterizationTool:
    """
    Determines the optimal tool based on a list of requested properties.

    The logic prefers the faster, local RDKit tool if it can satisfy all
    property requests. If not, it checks Stoplight. If neither tool can
    satisfy all requests alone, it returns 'COMBINED'.

    Args:
        properties: A list of normalized property names.

    Returns:
        The recommended CharacterizationTool to use.
    """
    normalized_props = {normalize_property_name(p) for p in properties}

    requires_boltz = any(prop in BOLTZ_PROPERTIES for prop in normalized_props)
    remaining_props = {prop for prop in normalized_props if prop not in BOLTZ_PROPERTIES}

    # Check if all requested properties can be fulfilled by RDKit.
    can_use_rdkit = all(
        p in RDKIT_PROPERTIES or (p in PROPERTY_MAPPINGS and PROPERTY_MAPPINGS[p][0])
        for p in remaining_props
    )

    # Check if all requested properties can be fulfilled by Stoplight.
    can_use_stoplight = all(
        p in STOPLIGHT_PROPERTIES or (p in PROPERTY_MAPPINGS and PROPERTY_MAPPINGS[p][1])
        for p in remaining_props
    )

    # Decision logic based on tool capabilities.
    if not remaining_props and requires_boltz:
        return CharacterizationTool.BOLTZ
    if can_use_rdkit:
        # Prefer RDKit if it can handle all properties, as it's local and faster.
        return CharacterizationTool.RDKIT
    elif can_use_stoplight:
        return CharacterizationTool.STOPLIGHT
    else:
        # If neither tool can handle all properties individually, both are needed.
        return CharacterizationTool.COMBINED


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
