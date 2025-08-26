# nodes/decide_library.py

def decide_library_node(state):
    """
    Decide whether to enumerate molecules or connect to external screening library.
    """
    tracker = state['tracker']
    
    # Log that we're making library decision
    tracker.log_action("starting_library_decision", {
        "molecule_source": tracker.molecule_source.value if tracker.molecule_source else None,
        "has_enumeration_params": tracker.enumeration_params is not None,
        "has_external_library_config": len(tracker.external_library_config) > 0
    })
    
    # TODO: Decision logic based on tracker state
    molecule_source = tracker.molecule_source
    has_starting_molecules = len(tracker.starting_molecules) > 0
    enumeration_params = tracker.enumeration_params
    external_library_config = tracker.external_library_config
    
    # TODO: Make decision
    # if molecule_source == MoleculeSource.SCREENING_LIBRARY:
    #     decision = "external_library"
    # elif has_starting_molecules and enumeration_params:
    #     decision = "enumerate"
    # else:
    #     decision = "enumerate"  # default
    
    # For now, placeholder decision
    decision = "enumerate"  # or "external_library"
    
    # Record the decision in tracker
    tracker.metadata["library_strategy"] = decision
    
    # TODO: Set up chosen strategy
    if decision == "enumerate":
        # Prepare for enumeration
        tracker.metadata["enumeration_ready"] = True
    elif decision == "external_library":
        # Prepare for external library connection
        tracker.metadata["external_library_ready"] = True
    
    # Log the decision
    tracker.log_action("library_decision_made", {
        "decision": decision,
        "reasoning": {
            "molecule_source": tracker.molecule_source.value if tracker.molecule_source else None,
            "has_starting_molecules": has_starting_molecules,
            "has_enumeration_params": enumeration_params is not None
        }
    })
    
    return state