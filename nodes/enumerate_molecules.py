"""
Enumerate molecules from starting molecules.
"""

from typing import Dict, Any
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas.state import WorkflowState
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
        state.log("enumerate_molecules_error", "No starting molecules available")
        return {"state": state}
    
    enumerator = EnumeratorTool()
    
    # Determine enumeration parameters
    max_molecules = state.parsed_arguments.get('enumeration_size', 100)
    
    all_molecules = {}
    molecule_counter = 0
    
    for starting_smiles in state.starting_molecules:
        try:
            # Call the enumerator tool
            print(f"Enumerating: {min(max_molecules // len(state.starting_molecules), 100)}")
            result = enumerator._run(
                molecule=starting_smiles,
                n_compositions=min(max_molecules // len(state.starting_molecules), 100),
            )
            
            # Parse the result (it returns a dict with molecule_id -> SMILES)
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
            
        except Exception as e:
            state.log("enumerate_molecules_error", {
                "starting_molecule": starting_smiles,
                "error": str(e)
            })
            print(f"❌ ERROR in enumerate_molecules_node for {starting_smiles}: {e}")
    
    # Update state with enumerated molecules
    state.search_space = all_molecules
    
    state.log("enumerate_molecules_completed", {
        "total_molecules": len(all_molecules),
        "molecule_ids": list(all_molecules.keys())[:10]
    })
    
    return state