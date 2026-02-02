"""
Setup node for initializing the workflow configuration.
"""
import os
from typing import Dict, Any
from schemas.state import WorkflowState, BOConfiguration, TargetProperty, OptimizationMode, WorkflowStatus


def setup_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Setup the workflow based on extracted arguments.
    Initialize BO configuration and other parameters.
    """
    state.log("setup_started")
    
    # Set default targets if none were extracted
    if not state.targets:
        state.targets = [
            TargetProperty(
                name="QED",
                mode=OptimizationMode.MAXIMIZE,
                weight=1.0,
                bounds=(0.0, 1.0)
            )
        ]
        state.log("setup_default_targets", {"targets": ["QED"]})
    
    batch_size = state.parsed_arguments.get('batch_size', 5)
    state.bo_config = BOConfiguration(
        batch_size=batch_size,
        max_iterations=state.max_iterations,
        n_initial_points=min(10, state.parsed_arguments.get('enumeration_size', 100) // 10),
        encoding=os.getenv("MOLECULAR_FP", "MORDRED")
    )
    
    if state.molecule_source:
        if state.molecule_source == "external_library":
            # Configure for external library usage
            state.library_config = {
                "source": "chembl",  # Default library
                "filters": {
                    "molecular_weight": (200, 500),
                    "logp": (-0.4, 5.6)
                }
            }
        elif state.molecule_source == "enumerated":
            if not state.starting_molecules:
                # Use a default starting molecule if none provided
                state.starting_molecules = ["CC(=O)OC1=CC=CC=C1C(=O)O"]  # Aspirin
                state.log("setup_default_starting_molecule", {"molecule": "Aspirin"})
    
    if not state.starting_molecules and state.molecule_source in ["provided", "enumerated"]:
        state.log("setup_warning", "No starting molecules provided, switching to generation mode")
        state.molecule_source = "generated"
    
    state.status = WorkflowStatus.RUNNING
    
    state.log("setup_completed", {
        "targets": [t.model_dump() for t in state.targets],
        "molecule_source": state.molecule_source,
        "bo_config": state.bo_config.model_dump(),
        "max_iterations": state.max_iterations
    })
    
    return state