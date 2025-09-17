"""
Decide which characterization tool to use based on target properties.
"""

from typing import Dict, Any
from schemas.state import WorkflowState
from schemas.characterization import (
    determine_best_tool,
    normalize_property_name,
)
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
    
    tool_choice = determine_best_tool(normalized_properties)
    
    if not hasattr(state, 'characterization_config'):
        state.characterization_config = {}
    
    state.characterization_config = {
        'tool': tool_choice,
        'properties': target_properties,
        'normalized_properties': normalized_properties
    }
    
    state.log("decide_characterization_completed", {
        "tool_selected": tool_choice,
        "properties_requested": target_properties,
        "molecules_to_characterize": len(state.current_bo_recommendations) if state.current_bo_recommendations else 0
    })
    
    return state