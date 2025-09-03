# nodes/connect_external_library.py

from StateTracker import MoleculeEntry, MoleculeSource

def connect_external_library_node(state):
    """
    Connect to external screening library and load molecules.
    """
    tracker = state['tracker']
    
    # Log that we're connecting to external library
    tracker.log_action("starting_external_library_connection", {
        "library_config": tracker.external_library_config,
        "target_objectives": [obj.value for obj in tracker.objectives] if tracker.objectives else []
    })
    
    # TODO: Get library connection parameters from tracker
    library_config = tracker.external_library_config
    target_properties = tracker.target_properties
    bo_params = tracker.bo_params
    
    # TODO: Connect to external library
    # library_connection = connect_to_library(library_config)
    # available_molecules = library_connection.get_molecules(filters=target_properties)
    
    # For now, placeholder external library molecules
    external_molecules = [
        "COC1=CC=C(C=C1)C(=O)O",
        "CC1=CC=CC=C1C(=O)O", 
        "NC1=CC=CC=C1C(=O)O",
        "OC1=CC=CC=C1C(=O)O"
    ]  # Dummy library molecules
    
    # TODO: Apply filters and sampling
    # if bo_params and bo_params.n_initial_points:
    #     sampled_molecules = sample_diverse_molecules(external_molecules, bo_params.n_initial_points)
    # else:
    #     sampled_molecules = external_molecules[:100]  # default sample
    
    sampled_molecules = external_molecules  # Placeholder
    
    # Add library molecules to tracker
    for smiles in sampled_molecules:
        molecule_entry = MoleculeEntry(
            smiles=smiles,
            source=MoleculeSource.SCREENING_LIBRARY,
            generation_round=tracker.current_generation,
            metadata={
                "library_source": library_config.get("name", "unknown"),
                "connection_method": library_config.get("method", "unknown")
            }
        )
        
        tracker.molecules[molecule_entry.id] = molecule_entry
    
    # Update molecule pool for BO
    tracker.molecule_pool.extend(sampled_molecules)
    
    # Record library connection info
    tracker.metadata["external_library_connected"] = True
    tracker.metadata["library_info"] = {
        "total_available": len(external_molecules),
        "molecules_loaded": len(sampled_molecules),
        "connection_successful": True
    }
    
    # Log connection results
    tracker.log_action("external_library_connected", {
        "molecules_loaded": len(sampled_molecules),
        "total_molecules_in_pool": len(tracker.molecule_pool),
        "library_info": tracker.metadata["library_info"]
    })
    
    return state