"""
Characterize molecules using the selected tool(s).
"""

from typing import Dict, Any, List
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rdkit import Chem
from rdkit.Chem import Descriptors
from schemas.state import WorkflowState, ExperimentResult
from schemas.errors import NodeError
from utils.telemetry import emit_event
from schemas.characterization import (
    CharacterizationTool,
    PROPERTY_MAPPINGS,
    normalize_property_name
)
from tools.molecule_characterization_tool import MoleculeCharacterizationTool
from tools.stoplight_tool import StoplightTool


def characterize_molecules_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Characterize molecules using the selected tool(s).
    This replaces the llm_experiment node for actual property calculation.
    """
    state.log("characterize_molecules_started", {
        "tool": state.characterization_config.get('tool', 'auto'),
        "molecules_count": len(state.current_bo_recommendations) if state.current_bo_recommendations else 0
    })
    
    if not state.current_bo_recommendations:
        emit_event(state, kind="no_recommendations", node="characterize_molecules", severity="error")
        raise NodeError("No molecules to characterize for this iteration", node="characterize_molecules", code="EMPTY_BATCH")
    
    # Get tool choice
    tool_choice = state.characterization_config.get('tool', CharacterizationTool.AUTO)
    
    results = {}
    
    # Use RDKit tool
    if tool_choice in [CharacterizationTool.RDKIT, CharacterizationTool.COMBINED]:
        try:
            rdkit_tool = MoleculeCharacterizationTool()
            rdkit_result = rdkit_tool._run(
                search_space=state.search_space,
                ids_to_process=state.current_bo_recommendations
            )
            
            # Extract results from memory
            if rdkit_result:
                for mol_id, props in rdkit_result.items():
                    if mol_id not in results:
                        results[mol_id] = {}
                    results[mol_id].update(props)
                    
            state.log("characterize_rdkit_completed", {
                "molecules_processed": len(results),
                "properties": list(next(iter(results.values())).keys()) if results else []
            })
        except Exception as e:
            emit_event(state, kind="rdkit_tool_exception", node="characterize_molecules", tool="MoleculeCharacterizer", severity="error", data={"error": str(e)})
    
    # Use Stoplight tool
    if tool_choice in [CharacterizationTool.STOPLIGHT, CharacterizationTool.COMBINED]:
        try:
            stoplight_tool = StoplightTool()
            stoplight_result = stoplight_tool._run(
                precision=2,
                search_space=state.search_space,
                ids_to_process=state.current_bo_recommendations
            )

            # Extract results from memory
            if stoplight_result:
                for mol_id, props in stoplight_result.items():
                    if mol_id not in results:
                        results[mol_id] = {}
                    results[mol_id].update(props)
                    
            state.log("characterize_stoplight_completed", {
                "molecules_processed": len(results),
                "api_response": stoplight_result
            })
        except Exception as e:
            emit_event(state, kind="stoplight_tool_exception", node="characterize_molecules", tool="Stoplight", severity="error", data={"error": str(e)})
    
    # Convert results to ExperimentResult objects
    mapped_any = False
    for mol_id in state.current_bo_recommendations:
        if mol_id in state.search_space and mol_id in results:
            smiles = state.search_space[mol_id]
            
            # Map properties to target names
            mapped_properties = {}
            for target in state.targets:
                target_name_lower = normalize_property_name(target.name)
                
                # Try to find the property in results
                found = False
                for result_key, result_value in results[mol_id].items():
                    result_key_lower = normalize_property_name(result_key)
                    
                    # Direct match
                    if result_key_lower == target_name_lower:
                        mapped_properties[target.name] = float(result_value)
                        found = True
                        break
                    
                    # Check mappings
                    if target_name_lower in PROPERTY_MAPPINGS:
                        rdkit_name, stoplight_name = PROPERTY_MAPPINGS[target_name_lower]
                        if (rdkit_name and normalize_property_name(rdkit_name) == result_key_lower) or \
                           (stoplight_name and normalize_property_name(stoplight_name) == result_key_lower):
                            mapped_properties[target.name] = float(result_value)
                            found = True
                            break
                
                # If not found, try to get any similar property
                if not found:
                    # Look for partial matches
                    for result_key, result_value in results[mol_id].items():
                        if target_name_lower in normalize_property_name(result_key) or \
                           normalize_property_name(result_key) in target_name_lower:
                            mapped_properties[target.name] = float(result_value)
                            break
            
            # Create ExperimentResult
            if mapped_properties:
                exp_result = ExperimentResult(
                    molecule_id=mol_id,
                    smiles=smiles,
                    iteration=state.current_iteration,
                    properties=mapped_properties,
                    metadata={
                        "characterization_tool": tool_choice,
                        "all_properties": results[mol_id]  # Store all calculated properties
                    }
                )
                state.add_experimental_result(exp_result)
                mapped_any = True

    if not mapped_any:
        emit_event(state, kind="no_properties_mapped", node="characterize_molecules", severity="error", data={"count": len(state.current_bo_recommendations)})
        raise NodeError("Characterization produced no mappable properties for targets", node="characterize_molecules", code="NO_USABLE_DATA")
    
    state.log("characterize_molecules_completed", {
        "tool_used": tool_choice,
        "molecules_characterized": len(results),
        "properties_mapped": len(state.experimental_results)
    })

    print(f"🔍 EXITING NODE: {characterize_molecules_node.__name__}")
    print(f"   - New iteration: {state.current_iteration}")
    print(f"   - New status: {state.status}")
    print(f"   - Should continue: {state.should_continue()}")
    
    return state