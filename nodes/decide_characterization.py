"""
Decide which characterization tool to use based on target properties.
"""

from typing import Dict, Any
from schemas.state import WorkflowState
from schemas.characterization import (
    normalize_property_name,
    select_characterization_tool_ids,
)
from schemas.tool_registry import ToolKind
from tools.registry import get_tool_registry
from utils.telemetry import emit_event


def decide_characterization_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Decide which characterization tool(s) to use based on target properties.
    """
    state.log("decide_characterization_started")
    

    target_properties = [t.name for t in state.targets]
    if not target_properties:
        emit_event(state, kind="no_targets", node="decide_characterization", severity="warning")
    
    normalized_properties = [normalize_property_name(p) for p in target_properties]

    requires_boltz = any(prop in {"binding_affinity", "affinity"} for prop in normalized_properties)
    registry = get_tool_registry()
    tool_ids = select_characterization_tool_ids(normalized_properties, registry=registry, context=state)

    if not tool_ids:
        emit_event(
            state,
            kind="no_characterization_tools",
            node="decide_characterization",
            severity="error",
            data={"properties": normalized_properties},
        )

    selected_specs = [registry.get(tool_id) for tool_id in tool_ids]
    
    if not hasattr(state, 'characterization_config'):
        state.characterization_config = {}

    for spec in selected_specs:
        state.record_tool_selection(
            registry.selection_for(
                stage=ToolKind.CHARACTERIZER,
                spec=spec,
                reason="Selected by characterization property coverage.",
            )
        )
    
    state.characterization_config = {
        'tool': _legacy_tool_label(tool_ids),
        'tool_ids': tool_ids,
        'properties': target_properties,
        'normalized_properties': normalized_properties,
        'requires_boltz': requires_boltz,
        'boltz_only': tool_ids == ['boltz'],
        'available_proteins': len(getattr(state, 'protein_targets', [])),
    }
    
    state.log("decide_characterization_completed", {
        "tool_selected": state.characterization_config['tool'],
        "tool_ids": tool_ids,
        "properties_requested": target_properties,
        "molecules_to_characterize": len(state.current_bo_recommendations) if state.current_bo_recommendations else 0,
        "requires_boltz": requires_boltz,
        "proteins_available": len(getattr(state, 'protein_targets', []))
    })
    
    return state


def _legacy_tool_label(tool_ids: list[str]) -> str:
    """Keep existing UI/log semantics while storing concrete tool IDs."""

    if tool_ids == ["rdkit"]:
        return "rdkit"
    if tool_ids == ["stoplight"]:
        return "stoplight"
    if tool_ids == ["boltz"]:
        return "boltz"
    if tool_ids:
        return "combined"
    return "auto"
