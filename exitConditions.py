# nodes/check_exit_conditions.py

def check_exit_conditions_node(state):
    """
    Evaluate all exit conditions to determine if optimization should continue or stop.
    """
    tracker = state['tracker']
    
    # Log that we're checking exit conditions
    tracker.log_action("checking_exit_conditions", {
        "iteration": tracker.current_iteration,
        "budget_used": {
            "iterations": tracker.current_iteration,
            "experiments": len(tracker.completed_experiments)
        }
    })
    
    # TODO: Check budget constraints
    max_iterations = tracker.budget.get("iterations", float('inf'))
    max_experiments = tracker.budget.get("experiments", float('inf'))
    
    budget_exceeded = False
    budget_reason = None
    
    if tracker.current_iteration >= max_iterations:
        budget_exceeded = True
        budget_reason = "max_iterations_reached"
    elif len(tracker.completed_experiments) >= max_experiments:
        budget_exceeded = True
        budget_reason = "max_experiments_reached"
    
    # TODO: Check convergence conditions
    convergence_met = False
    convergence_reason = None
    
    if tracker.bo_params and len(tracker.convergence_history) >= tracker.bo_params.convergence_patience:
        # Check if improvement is below threshold for patience iterations
        recent_scores = tracker.convergence_history[-tracker.bo_params.convergence_patience:]
        improvement = max(recent_scores) - min(recent_scores)
        
        if improvement < tracker.bo_params.convergence_threshold:
            convergence_met = True
            convergence_reason = "convergence_threshold_met"
    
    # TODO: Check target achievement
    target_achieved = False
    target_reason = None
    
    if tracker.best_molecules:
        best_score = max([score for _, score in tracker.best_molecules])
        # TODO: Define target score based on objectives
        target_score = 0.9  # Placeholder target
        
        if best_score >= target_score:
            target_achieved = True
            target_reason = "target_score_achieved"
    
    # TODO: Check error conditions
    error_exit = False
    error_reason = None
    
    # Check for too many failed experiments
    failed_experiments = len([exp for exp in tracker.experiments.values() 
                            if exp.status.value == "failed"])
    
    if failed_experiments > 5:  # TODO: make configurable
        error_exit = True
        error_reason = "too_many_failed_experiments"
    
    # TODO: Check resource limits (time, memory, etc.)
    # resource_exceeded = check_resource_limits(tracker)
    resource_exceeded = False
    resource_reason = None
    
    # TODO: Check for user interruption
    # user_interrupted = check_user_interruption()
    user_interrupted = False
    
    # Determine overall exit decision
    should_exit = (budget_exceeded or convergence_met or target_achieved or 
                   error_exit or resource_exceeded or user_interrupted)
    
    # Determine exit reason (priority order)
    exit_reason = None
    if error_exit:
        exit_reason = error_reason
    elif target_achieved:
        exit_reason = target_reason
    elif convergence_met:
        exit_reason = convergence_reason
    elif budget_exceeded:
        exit_reason = budget_reason
    elif resource_exceeded:
        exit_reason = resource_reason
    elif user_interrupted:
        exit_reason = "user_interrupted"
    
    # Update tracker with exit decision
    if should_exit:
        tracker.is_complete = True
        tracker.exit_condition_met = exit_reason
    
    # Record exit condition analysis
    exit_analysis = {
        "should_exit": should_exit,
        "exit_reason": exit_reason,
        "condition_checks": {
            "budget_exceeded": budget_exceeded,
            "convergence_met": convergence_met,
            "target_achieved": target_achieved,
            "error_exit": error_exit,
            "resource_exceeded": resource_exceeded,
            "user_interrupted": user_interrupted
        },
        "current_status": {
            "iteration": tracker.current_iteration,
            "max_iterations": max_iterations,
            "experiments_completed": len(tracker.completed_experiments),
            "max_experiments": max_experiments,
            "best_score": max([score for _, score in tracker.best_molecules]) if tracker.best_molecules else None,
            "convergence_history_length": len(tracker.convergence_history)
        }
    }
    
    # Store analysis in tracker
    tracker.metadata["exit_condition_analysis"] = exit_analysis
    
    # Log exit condition decision
    tracker.log_action("exit_conditions_evaluated", {
        "should_exit": should_exit,
        "exit_reason": exit_reason,
        "iteration": tracker.current_iteration,
        "budget_remaining": {
            "iterations": max_iterations - tracker.current_iteration,
            "experiments": max_experiments - len(tracker.completed_experiments)
        },
        "conditions_met": [k for k, v in exit_analysis["condition_checks"].items() if v]
    })
    
    return state