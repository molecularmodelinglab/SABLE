# nodes/summarize_results.py

def summarize_results_node(state):
    """
    Generate final summary of the entire molecular optimization campaign.
    """
    tracker = state['tracker']
    
    # Log that we're creating final summary
    tracker.log_action("starting_final_summary", {
        "total_iterations": tracker.current_iteration,
        "completion_reason": tracker.exit_condition_met,
        "campaign_duration": (tracker.logs[-1]["timestamp"] if tracker.logs else None)
    })
    
    # TODO: Gather all campaign data
    original_prompt = tracker.original_prompt
    objectives = tracker.objectives
    total_iterations = tracker.current_iteration
    total_molecules_tested = len(tracker.molecules)
    total_experiments = len(tracker.completed_experiments)
    best_molecules = tracker.best_molecules
    exit_condition = tracker.exit_condition_met
    
    # TODO: Calculate campaign statistics
    # success_rate = calculate_success_rate(tracker.experiments)
    # improvement_over_time = analyze_convergence(tracker.convergence_history)
    # property_distributions = analyze_property_distributions(tracker.molecules)
    
    # For now, placeholder statistics
    campaign_stats = {
        "total_molecules_generated": len([m for m in tracker.molecules.values() if m.source.value == "generated"]),
        "total_molecules_enumerated": len([m for m in tracker.molecules.values() if m.source.value == "enumerated"]),
        "total_molecules_from_library": len([m for m in tracker.molecules.values() if m.source.value == "screening_library"]),
        "experiments_successful": len(tracker.completed_experiments),
        "experiments_failed": len(tracker.experiments) - len(tracker.completed_experiments),
        "final_best_score": max([score for _, score in tracker.best_molecules]) if tracker.best_molecules else None
    }
    
    # TODO: Generate insights and recommendations
    # key_findings = analyze_structure_activity_relationships(tracker.molecules)
    # optimization_insights = analyze_bo_performance(tracker.bo_history)
    # recommendations = generate_recommendations(campaign_stats, key_findings)
    
    # For now, placeholder insights
    insights = {
        "optimization_successful": campaign_stats["final_best_score"] is not None,
        "convergence_achieved": exit_condition == "converged",
        "budget_utilization": {
            "iterations_used": total_iterations,
            "experiments_used": total_experiments,
            "efficiency": total_experiments / tracker.budget["experiments"] if tracker.budget.get("experiments") else 0
        }
    }
    
    # Create comprehensive summary
    final_summary = {
        "research_id": tracker.research_id,
        "original_prompt": original_prompt,
        "campaign_objectives": [obj.value for obj in objectives] if objectives else [],
        "completion_status": {
            "completed": tracker.is_complete,
            "exit_condition": exit_condition,
            "total_iterations": total_iterations,
            "final_iteration_reached": total_iterations >= tracker.budget.get("iterations", 0)
        },
        "molecular_results": {
            "total_molecules_explored": total_molecules_tested,
            "best_molecules": best_molecules[:5],  # Top 5
            "molecule_sources": {
                "generated": campaign_stats["total_molecules_generated"],
                "enumerated": campaign_stats["total_molecules_enumerated"],
                "library": campaign_stats["total_molecules_from_library"]
            }
        },
        "experimental_results": {
            "total_experiments": total_experiments,
            "successful_experiments": campaign_stats["experiments_successful"],
            "failed_experiments": campaign_stats["experiments_failed"],
            "success_rate": campaign_stats["experiments_successful"] / total_experiments if total_experiments > 0 else 0
        },
        "optimization_performance": insights,
        "timestamps": {
            "started": tracker.created_at.isoformat(),
            "completed": tracker.logs[-1]["timestamp"] if tracker.logs else None
        }
    }
    
    # Store summary in tracker
    tracker.metadata["final_summary"] = final_summary
    tracker.is_complete = True
    
    # TODO: Generate human-readable report
    # human_readable_report = generate_report(final_summary)
    # tracker.metadata["final_report"] = human_readable_report
    
    # Log final summary
    tracker.log_action("final_summary_completed", {
        "summary_generated": True,
        "total_molecules": final_summary["molecular_results"]["total_molecules_explored"],
        "best_score": campaign_stats["final_best_score"],
        "campaign_successful": insights["optimization_successful"]
    })
    
    return state          