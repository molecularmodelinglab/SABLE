"""
Summarize the optimization results.
"""

from typing import Dict, Any
from schemas.state import WorkflowState


def summarize_results_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Generate a summary of the optimization results.
    """
    state.log("summarize_results_started")
    
    total_molecules_tested = len(state.experimental_results)
    total_iterations = state.current_iteration + 1
    
    summary_parts = [
        f"=== Molecular Optimization Summary ===",
        f"Workflow ID: {state.workflow_id}",
        f"Status: {state.status}",
        f"Exit Reason: {state.exit_reason or 'Completed normally'}",
        f"",
        f"Optimization Configuration:",
        f"- Target Properties: {', '.join([t.name for t in state.targets])}",
        f"- Molecule Source: {state.molecule_source}",
        f"- Total Iterations: {total_iterations}",
        f"- Molecules Tested: {total_molecules_tested}",
        f"- Batch Size: {state.bo_config.batch_size if state.bo_config else 'N/A'}",
        f""
    ]
    
    if state.starting_molecules:
        summary_parts.append("Starting Molecules:")
        for i, smiles in enumerate(state.starting_molecules, 1):
            summary_parts.append(f"  {i}. {smiles}")
        summary_parts.append("")
    
    if state.best_molecules:
        summary_parts.append(f"Top {min(5, len(state.best_molecules))} Optimized Molecules:")
        for i, (smiles, score) in enumerate(state.best_molecules[:5], 1):
            summary_parts.append(f"  {i}. Score: {score:.4f}")
            summary_parts.append(f"     SMILES: {smiles}")
            
            # Find properties for this molecule
            for result in state.experimental_results:
                if result.smiles == smiles:
                    props_str = ", ".join([f"{k}: {v:.3f}" for k, v in result.properties.items()])
                    summary_parts.append(f"     Properties: {props_str}")
                    break
        summary_parts.append("")
    
    if state.bo_rounds:
        summary_parts.append("Optimization Progress:")
        for round_data in state.bo_rounds[-3:]:  # Last 3 rounds
            summary_parts.append(f"  Iteration {round_data['iteration']}: "
                               f"Tested {len(round_data.get('recommendations', []))} molecules")
    
    # Add search space statistics
    if state.search_space:
        tested_smiles = {r.smiles for r in state.experimental_results}
        summary_parts.extend([
            "",
            "Search Space Statistics:",
            f"- Total Molecules in Space: {len(state.search_space)}",
            f"- Molecules Tested: {len(tested_smiles)}",
            f"- Coverage: {len(tested_smiles)/len(state.search_space)*100:.1f}%"
        ])
    
    state.summary = "\n".join(summary_parts)
    
    state.log("summarize_results_completed", {
        "summary_length": len(state.summary),
        "best_score": state.best_molecules[0][1] if state.best_molecules else None
    })
    
    print("\n" + state.summary)
    
    return state