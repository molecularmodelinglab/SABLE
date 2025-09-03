from enum import Enum
from typing import Any, Dict, List
from pydantic import BaseModel, Field



class CharacterizationTool(str, Enum):
    """Enumeration for the available molecular characterization tools."""
    RDKIT = "rdkit"
    STOPLIGHT = "stoplight"
    COMBINED = "combined"    # Use both RDKit and Stoplight
    AUTO = "auto"          # Automatically decide the best tool


class PropertySource(str, Enum):
    """Enumeration for the source of a calculated property value."""
    RDKIT = "rdkit"
    STOPLIGHT = "stoplight"
    LLM = "llm"
    EXPERIMENTAL = "experimental"

# Sets of standardized property names available from each tool.
RDKIT_PROPERTIES = {
    "molecular_formula", "exact_mass", "molecular_weight", "heavy_atom_count",
    "ring_count", "aromatic_ring_count", "h_bond_donors", "h_bond_acceptors",
    "rotatable_bonds", "logp", "tpsa", "qed"
}

STOPLIGHT_PROPERTIES = {
    "alogp", "ampc_aggregation", "cns_activity", "cruzain_aggregation",
    "fsp3", "firefly_luciferase", "hba", "hbd", "molecular_weight",
    "nano_luciferase", "num_heavy_atoms", "num_quaternary_carbons",
    "number_of_rings", "number_of_rotatable_bonds", "polar_surface_area",
    "redox_interference", "solubility_water", "thiol_interference"
}

# Dictionary to map common aliases to standardized property names.
# Format: "standardized_name": (RDKit internal name, Stoplight internal name)
PROPERTY_MAPPINGS = {
    "molecular_weight": ("Molecular_Weight", "Molecular Weight"),
    "mw": ("Molecular_Weight", "Molecular Weight"),
    "logp": ("LogP", "ALogP"),
    "alogp": ("LogP", "ALogP"),
    "tpsa": ("TPSA", "Polar Surface Area"),
    "polar_surface_area": ("TPSA", "Polar Surface Area"),
    "psa": ("TPSA", "Polar Surface Area"),
    "qed": ("QED", None),
    "h_bond_donors": ("H_Bond_Donors", "HBD"),
    "hbd": ("H_Bond_Donors", "HBD"),
    "h_bond_acceptors": ("H_Bond_Acceptors", "HBA"),
    "hba": ("H_Bond_Acceptors", "HBA"),
    "rotatable_bonds": ("Rotatable_Bonds", "Number of Rotatable Bonds"),
    "solubility": (None, "Solubility in Water (mg/L)"),
    "ring_count": ("Ring_Count", "Number of Rings"),
    "heavy_atoms": ("Heavy_Atom_Count", "Num Heavy Atoms"),
}



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

    # Check if all requested properties can be fulfilled by RDKit.
    can_use_rdkit = all(
        p in RDKIT_PROPERTIES or (p in PROPERTY_MAPPINGS and PROPERTY_MAPPINGS[p][0])
        for p in normalized_props
    )

    # Check if all requested properties can be fulfilled by Stoplight.
    can_use_stoplight = all(
        p in STOPLIGHT_PROPERTIES or (p in PROPERTY_MAPPINGS and PROPERTY_MAPPINGS[p][1])
        for p in normalized_props
    )

    # Decision logic based on tool capabilities.
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
    return prop.lower().replace(" ", "_").replace("-", "_")
