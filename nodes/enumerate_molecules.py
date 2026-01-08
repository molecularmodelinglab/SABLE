"""
Enumerate molecules from starting molecules.
"""

from typing import Dict, Any
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas.state import WorkflowState
from schemas.errors import NodeError, ToolError
from utils.telemetry import emit_event
from tools.enumerator_tool import EnumeratorTool

def enumerate_molecules_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Enumerate molecules from starting molecules using the EnumeratorTool.
    """

    print(f"🔍 ENTERING NODE: {enumerate_molecules_node.__name__}")
    print(f"   - Current iteration: {state.current_iteration}")
    print(f"   - Max iterations: {state.max_iterations}")
    print(f"   - Status: {state.status}")
    print(f"   - Should continue: {state.should_continue()}")

    state.log("enumerate_molecules_started", {
        "starting_molecules": state.starting_molecules
    })
    
    if not state.starting_molecules:
        emit_event(state, kind="no_starting_molecules", node="enumerate_molecules", severity="error")
        raise NodeError(
            "No starting molecules available for enumeration",
            node="enumerate_molecules",
            code="EMPTY_STARTING_SET",
            details={"hint": "Provide starting_molecules or adjust molecule_source"},
        )
    
    # Determine enumeration parameters
    max_molecules = state.parsed_arguments.get('enumeration_size', 100)
    healer_mode = state.parsed_arguments.get('healer_mode', 'MoleculeHEALER')

    all_molecules = {}
    molecule_counter = 0

    if healer_mode == "MoleculeHEALER":
        mol_enumerator = EnumeratorTool(healer_mode=healer_mode)
    elif healer_mode == "FragmentHEALER":
        frag_enumerator = EnumeratorTool(healer_mode=healer_mode)
    elif healer_mode == "SiteHEALER":
        site_enumerator = EnumeratorTool(healer_mode=healer_mode)
    
    for starting_smiles in state.starting_molecules:
        try:
            # Call the enumerator tool
            print(f"Enumerating: {min(max_molecules // len(state.starting_molecules), 100)} using mode {healer_mode}")
            if healer_mode == "MoleculeHEALER":
                result = mol_enumerator._run(
                    molecule=starting_smiles,
                    n_compositions=min(max_molecules // len(state.starting_molecules), 100),
                )
            elif healer_mode == "FragmentHEALER":
                result = frag_enumerator._run(
                    molecule=starting_smiles,
                    n_compositions=min(max_molecules // len(state.starting_molecules), 100),
                )
            elif healer_mode == "SiteHEALER":
                result = site_enumerator._run(
                    molecule=starting_smiles,
                    n_compositions=min(max_molecules // len(state.starting_molecules), 100),
                )


            # Validate tool output
            if isinstance(result, str):
                emit_event(state, kind="enumerator_tool_error", node="enumerate_molecules", tool="Enumerator", severity="error", data={"message": result})
                raise ToolError(result, node="enumerate_molecules", tool="Enumerator", code="ENUMERATOR_FAILED")

            # Parse the result (must be dict with molecule_id -> SMILES)
            if isinstance(result, dict):
                for _, smiles in result.items():
                    # Create unique IDs for our state
                    unique_id = f"enum_{molecule_counter:04d}"
                    all_molecules[unique_id] = smiles
                    molecule_counter += 1
            
            state.log("enumerate_molecules_batch", {
                "starting_molecule": starting_smiles,
                "generated_count": len(result) if isinstance(result, dict) else 0
            })
            
        except ToolError as e:
            emit_event(state, kind="enumerate_batch_failed", node="enumerate_molecules", severity="error", data={"starting": starting_smiles, "error": str(e)})
            raise
        except Exception as e:
            state.log("enumerate_molecules_error", {
                "starting_molecule": starting_smiles,
                "error": str(e)
            })
            emit_event(state, kind="unexpected_exception", node="enumerate_molecules", severity="error", data={"starting": starting_smiles, "error": str(e)})
            raise NodeError(str(e), node="enumerate_molecules", code="ENUMERATE_EXCEPTION")
    
    # Validate and update state with enumerated molecules
    if not all_molecules:
        emit_event(state, kind="empty_search_space", node="enumerate_molecules", severity="error")
        raise NodeError(
            "Enumeration produced an empty search space",
            node="enumerate_molecules",
            code="EMPTY_SEARCH_SPACE",
            details={"starting_smiles_count": len(state.starting_molecules)},
        )

    state.search_space = all_molecules
    
    # Assess feasibility vs. planned iterations and batch size
    batch_size = state.bo_config.batch_size if state.bo_config else 5
    tested_smiles = {r.smiles for r in state.experimental_results}
    remaining_count = len(set(state.search_space.values()) - tested_smiles)
    max_additional_rounds = remaining_count // max(1, batch_size)
    if state.max_iterations > max_additional_rounds:
        old = state.max_iterations
        state.max_iterations = max_additional_rounds
        emit_event(
            state,
            kind="max_iterations_adjusted",
            node="enumerate_molecules",
            severity="warning",
            data={
                "old_max_iterations": old,
                "new_max_iterations": state.max_iterations,
                "remaining_molecules": remaining_count,
                "batch_size": batch_size,
            },
        )
        if state.max_iterations == 0:
            emit_event(
                state,
                kind="insufficient_for_first_batch",
                node="enumerate_molecules",
                severity="error",
                data={"remaining_molecules": remaining_count, "batch_size": batch_size},
            )
    
    state.log("enumerate_molecules_completed", {
        "total_molecules": len(all_molecules),
        "molecule_ids": list(all_molecules.keys())[:10]
    })
    
    return state