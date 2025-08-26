# nodes/setup.py

from stateTracking import OptimizationObjective, MoleculeSource, BOParameters, EnumerationParameters

def setup_node(state):
    """
    Setup the research configuration based on extracted arguments.
    Records all static parameters in the tracker.
    """
    tracker = state['tracker']
    
    # Log that we're starting setup
    tracker.log_action("starting_setup", {"iteration": tracker.current_iteration})
    
    # TODO: Get extracted parameters from previous node
    # For now, use dummy extracted parameters
    extracted_params = {
        "objectives": ["qed", "solubility"],
        "molecule_source": "generated", 
        "budget": {"iterations": 10, "experiments": 100},
        "starting_molecule": "CCC1C(=O)N(C)C...",
        "enumeration_requested": True,
        "max_molecules": 1000
    }
    
    # Set up objectives
    # TODO: Convert string objectives to enum
    # tracker.objectives = [OptimizationObjective.QED, OptimizationObjective.SOLUBILITY]
    
    # Set up molecule source
    # TODO: Convert string to enum
    # tracker.molecule_source = MoleculeSource.GENERATED
    
    # Set up budget
    tracker.budget = extracted_params["budget"]
    
    # Set up BO parameters
    tracker.bo_params = BOParameters(
        max_iterations=extracted_params["budget"]["iterations"],
        batch_size=5,
        n_initial_points=10
    )
    
    # Set up enumeration parameters if requested
    if extracted_params.get("enumeration_requested"):
        tracker.enumeration_params = EnumerationParameters(
            max_molecules=extracted_params.get("max_molecules", 1000),
            diversity_threshold=0.8
        )
    
    # Record starting molecule if provided
    if extracted_params.get("starting_molecule"):
        tracker.starting_molecules = [extracted_params["starting_molecule"]]
    
    # Set up target properties (static configuration)
    tracker.target_properties = {
        "qed": {"min": 0.0, "max": 1.0, "weight": 1.0},
        "solubility": {"min": -5.0, "max": 2.0, "weight": 0.5}
    }
    
    # Log all the static parameters we just set up
    tracker.log_action("setup_completed", {
        "bo_params": {
            "max_iterations": tracker.bo_params.max_iterations,
            "batch_size": tracker.bo_params.batch_size,
            "n_initial_points": tracker.bo_params.n_initial_points
        },
        "budget": tracker.budget,
        "has_enumeration_params": tracker.enumeration_params is not None,
        "has_starting_molecule": len(tracker.starting_molecules) > 0,
        "target_properties": list(tracker.target_properties.keys())
    })
    
    return state