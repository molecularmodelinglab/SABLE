"""
Check exit conditions for the optimization loop.
"""

from typing import Dict, Any
import time
from schemas.state import WorkflowState, WorkflowStatus
from utils.telemetry import emit_event


def _record_iteration_timing(state: WorkflowState) -> None:
    iteration_key = str(state.current_iteration)
    iteration_timers = state.profiling.setdefault("iteration_timers", {})
    iteration_summary = state.profiling.setdefault("iterations", [])
    timer = iteration_timers.get(iteration_key)
    if not timer:
        return

    if timer.get("ended_at") is None:
        timer["ended_at"] = time.time()
    elapsed_seconds = round(float(timer["ended_at"] - timer["started_at"]), 3)
    timer["elapsed_seconds"] = elapsed_seconds

    if not any(item.get("iteration") == state.current_iteration for item in iteration_summary):
        iteration_summary.append({
            "iteration": state.current_iteration,
            "elapsed_seconds": elapsed_seconds,
        })
        state.log("iteration_timing", {
            "iteration": state.current_iteration,
            "elapsed_seconds": elapsed_seconds,
        })


def check_exit_conditions_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Check if exit conditions are met for the optimization.
    """
    state.log("check_exit_conditions_started", {
        "iteration": state.current_iteration,
        "max_iterations": state.max_iterations
    })
    
    # Hard stop if search space is empty
    if not state.search_space:
        state.exit_reason = "Search space is empty"
        state.status = WorkflowStatus.FAILED
        emit_event(state, kind="empty_search_space", node="check_exit_conditions", severity="error")
        return state
    
    if state.current_iteration >= state.max_iterations - 1:
        _record_iteration_timing(state)
        state.exit_reason = f"Maximum iterations reached ({state.max_iterations})"
        state.status = WorkflowStatus.COMPLETED
        state.log("check_exit_conditions_max_iterations")
        return state
    
    # Check convergence based on best molecule scores
    if len(state.best_molecules) >= 5:
        # Check if top molecules haven't changed significantly
        recent_scores = [score for _, score in state.best_molecules[:5]]
        if len(set(recent_scores)) == 1:  # All same score
            _record_iteration_timing(state)
            state.exit_reason = "Convergence detected - top molecules have identical scores"
            state.status = WorkflowStatus.COMPLETED
            state.log("check_exit_conditions_converged")
            return state
        
        # Check if improvement is minimal
        if len(state.bo_rounds) >= 3:
            score_improvement = max(recent_scores) - min(recent_scores)
            if score_improvement < 0.01:  # Less than 1% improvement
                _record_iteration_timing(state)
                state.exit_reason = "Convergence detected - minimal improvement in recent iterations"
                state.status = WorkflowStatus.COMPLETED
                state.log("check_exit_conditions_minimal_improvement", {
                    "score_improvement": score_improvement
                })
                return state

    # Check if search space is exhausted
    tested_smiles = {r.smiles for r in state.experimental_results}
    available_smiles = set(state.search_space.values())
    remaining = available_smiles - tested_smiles
    
    if len(remaining) == 0:
        _record_iteration_timing(state)
        state.exit_reason = "Search space exhausted - all molecules tested"
        state.status = WorkflowStatus.COMPLETED
        state.log("check_exit_conditions_exhausted")
        return state
    
    required = state.bo_config.batch_size if state.bo_config else 5
    if len(remaining) < required:
        _record_iteration_timing(state)
        state.exit_reason = f"Insufficient molecules remaining ({len(remaining)}) for next batch (requires {required})"
        state.status = WorkflowStatus.COMPLETED
        state.log("check_exit_conditions_insufficient_molecules", {
            "remaining": len(remaining),
            "required": required
        })
        return state
    
    # Ensure BO produced recommendations in previous step
    if not state.current_bo_recommendations:
        _record_iteration_timing(state)
        state.exit_reason = "No BO recommendations available"
        state.status = WorkflowStatus.FAILED
        state.log("check_exit_conditions_no_recommendations")
        return state

    # No exit conditions met, continue
    _record_iteration_timing(state)
    state.current_iteration += 1
    state.log("check_exit_conditions_continue", {
        "next_iteration": state.current_iteration,
        "molecules_tested": len(tested_smiles),
        "molecules_remaining": len(remaining)
    })
    
    return state