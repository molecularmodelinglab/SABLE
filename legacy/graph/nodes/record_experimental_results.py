# nodes/record_experimental_results.py

import json
import os
from datetime import datetime

def record_experimental_results_node(state):
    """
    Record experimental results in BO-focused JSON format for analysis.
    """
    tracker = state['tracker']
    
    # Log that we're recording experimental results
    tracker.log_action("starting_experimental_results_recording", {
        "iteration": tracker.current_iteration,
        "completed_experiments": len(tracker.completed_experiments),
        "total_molecules_tested": len([m for m in tracker.molecules.values() if m.experimental_results])
    })
    
    # TODO: Create results filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"bo_results_{tracker.research_id}_iter_{tracker.current_iteration}_{timestamp}.json"
    filepath = os.path.join("results", filename)  # TODO: configure results directory
    
    # TODO: Ensure results directory exists
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    # TODO: Collect experimental data in BO format
    bo_results = {
        "experiment_metadata": {
            "research_id": tracker.research_id,
            "iteration": tracker.current_iteration,
            "timestamp": timestamp,
            "objectives": [obj.value for obj in tracker.objectives] if tracker.objectives else [],
            "bo_parameters": {
                "acquisition_function": tracker.bo_params.acquisition_function if tracker.bo_params else None,
                "batch_size": tracker.bo_params.batch_size if tracker.bo_params else None,
                "kernel": tracker.bo_params.kernel if tracker.bo_params else None
            } if tracker.bo_params else None
        },
        "experimental_data": [],  # TODO: populate with molecule-result pairs
        "bo_iteration_summary": {
            "molecules_tested_this_iteration": len(tracker.current_candidates),
            "total_molecules_tested": len([m for m in tracker.molecules.values() if m.experimental_results]),
            "best_score_this_iteration": None,  # TODO: calculate
            "best_score_overall": max([score for _, score in tracker.best_molecules]) if tracker.best_molecules else None
        },
        "acquisition_data": {},  # TODO: add acquisition scores from BO history
        "convergence_data": tracker.convergence_history
    }
    
    # TODO: Extract experimental data for each tested molecule
    for mol_id, molecule in tracker.molecules.items():
        if molecule.experimental_results:  # Only include molecules with experimental data
            
            # TODO: Calculate composite score based on objectives
            # composite_score = calculate_composite_score(molecule.experimental_results, tracker.objectives)
            composite_score = molecule.bo_score or molecule.experimental_results.get("qed", 0.0)  # Placeholder
            
            molecule_data = {
                "molecule_id": molecule.id,
                "smiles": molecule.smiles,
                "source": molecule.source.value,
                "generation_round": molecule.generation_round,
                "experimental_results": molecule.experimental_results,
                "bo_score": composite_score,
                "properties": molecule.properties,  # Basic calculated properties
                "tested_in_iteration": molecule.generation_round  # TODO: track which iteration molecule was tested
            }
            
            bo_results["experimental_data"].append(molecule_data)
    
    # TODO: Add acquisition scores from latest BO iteration
    if tracker.bo_history and len(tracker.bo_history) > 0:
        latest_bo_iteration = tracker.bo_history[-1]
        bo_results["acquisition_data"] = {
            "iteration": latest_bo_iteration.get("iteration", tracker.current_iteration),
            "acquisition_scores": latest_bo_iteration.get("acquisition_scores", {}),
            "selected_molecules": latest_bo_iteration.get("selected_molecules", [])
        }
    
    # TODO: Calculate iteration summary statistics
    if bo_results["experimental_data"]:
        iteration_scores = [data["bo_score"] for data in bo_results["experimental_data"] 
                          if data.get("tested_in_iteration") == tracker.current_iteration]
        
        bo_results["bo_iteration_summary"]["best_score_this_iteration"] = max(iteration_scores) if iteration_scores else None
        bo_results["bo_iteration_summary"]["average_score_this_iteration"] = sum(iteration_scores) / len(iteration_scores) if iteration_scores else None
    
    # TODO: Write results to JSON file
    try:
        with open(filepath, 'w') as f:
            json.dump(bo_results, f, indent=2, default=str)
        
        recording_successful = True
        error_message = None
        
    except Exception as e:
        recording_successful = False
        error_message = str(e)
    
    # Update tracker with recording info
    tracker.metadata[f"experimental_results_export_iter_{tracker.current_iteration}"] = {
        "filepath": filepath,
        "timestamp": timestamp,
        "successful": recording_successful,
        "error": error_message,
        "molecules_recorded": len(bo_results["experimental_data"]),
        "iteration": tracker.current_iteration
    }
    
    # TODO: Optional - also create CSV for easy analysis
    # csv_filepath = filepath.replace(".json", ".csv")
    # export_results_to_csv(bo_results["experimental_data"], csv_filepath)
    
    # Log recording results
    tracker.log_action("experimental_results_recorded", {
        "filepath": filepath,
        "successful": recording_successful,
        "error": error_message,
        "molecules_recorded": len(bo_results["experimental_data"]),
        "iteration": tracker.current_iteration,
        "best_score": bo_results["bo_iteration_summary"]["best_score_overall"]
    })
    
    return state