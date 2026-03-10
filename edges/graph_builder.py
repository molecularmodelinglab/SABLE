"""
Graph construction and edge routing for the molecular optimization workflow.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Dict, Any
from langgraph.graph import StateGraph, END
from schemas.state import WorkflowState, MoleculeSource

from nodes.extract_arguments_hybrid import extract_arguments_node as extract_arguments_hybrid_node
from nodes.extract_arguments import extract_arguments_node
from nodes.setup import setup_node
from nodes.enumerate_molecules import enumerate_molecules_node
from nodes.bo_iteration import bo_iteration_node
from nodes.decide_characterization import decide_characterization_node
from nodes.characterize_molecules import characterize_molecules_node
# from nodes.llm_experiment import llm_experiment_node
from nodes.check_exit_conditions import check_exit_conditions_node
from nodes.summarize_results import summarize_results_node



def route_after_setup(state: WorkflowState) -> str:
    """
    Route after setup based on molecule source.
    """
    # if state.molecule_source == MoleculeSource.EXTERNAL_LIBRARY:
    #     # Skip to BO with external library
    #     # TODO: Implement external library connection
    #     return "bo_iteration"
    if state.molecule_source in [MoleculeSource.PROVIDED, MoleculeSource.ENUMERATED]:
        if state.starting_molecules:
            return "enumerate_molecules"
        else:
            # No starting molecules, generate some
            # TODO: Implement molecule generation
            return "enumerate_molecules"
    else:
        # Generated molecules - TODO: implement generation
        return "enumerate_molecules"


def route_after_exit_check(state: WorkflowState) -> str:
    """
    Route after checking exit conditions.
    """
    if state.should_continue():
        return "bo_iteration"
    else:
        return "summarize_results"


def route_characterization_choice(state: WorkflowState) -> str:
    """
    Route based on characterization tool choice.
    Could add LLM experiment as alternative in future.
    """
    # For now, always use characterization tools
    # In future, could route to llm_experiment for certain cases
    return "characterize_molecules"


def build_molecular_graph() -> StateGraph:
    """
    Build the complete molecular optimization graph.
    
    Flow:
    1. Extract arguments from user prompt
    2. Setup configuration
    3. Enumerate molecules ONCE (or load external library) to create search space
    4. Enter BO loop:
       - BO selects molecules from search space
       - Characterize selected molecules
       - Check exit conditions
       - Loop back to BO or exit
    """

    graph = StateGraph(WorkflowState)
    
    graph.add_node("extract_arguments", extract_arguments_hybrid_node)
    graph.add_node("setup", setup_node)
    graph.add_node("enumerate_molecules", enumerate_molecules_node)
    graph.add_node("bo_iteration", bo_iteration_node)
    graph.add_node("decide_characterization", decide_characterization_node)
    graph.add_node("characterize_molecules", characterize_molecules_node)
    # graph.add_node("llm_experiment", llm_experiment_node)
    graph.add_node("check_exit_conditions", check_exit_conditions_node)
    graph.add_node("summarize_results", summarize_results_node)
    

    graph.add_edge("extract_arguments", "setup")
    
    graph.add_conditional_edges(
        "setup",
        route_after_setup,
        {
            "enumerate_molecules": "enumerate_molecules",
            "bo_iteration": "bo_iteration"  # Skip enumeration for external library
        }
    )
    
    graph.add_edge("enumerate_molecules", "bo_iteration")
    
    # BO loop: BO → Characterization → Check → (Continue to BO or Exit)
    graph.add_edge("bo_iteration", "decide_characterization")
    
    graph.add_conditional_edges(
        "decide_characterization",
        route_characterization_choice,
        {
            "characterize_molecules": "characterize_molecules",
            # "llm_experiment": "llm_experiment"  # Alternative path for future
        }
    )
    
    graph.add_edge("characterize_molecules", "check_exit_conditions")
    # graph.add_edge("llm_experiment", "check_exit_conditions")
    
    graph.add_conditional_edges(
        "check_exit_conditions",
        route_after_exit_check,
        {
            "bo_iteration": "bo_iteration",
            "summarize_results": "summarize_results"
        }
    )
    
    graph.add_edge("summarize_results", END)
    
    graph.set_entry_point("extract_arguments")
    
    return graph


def compile_graph():
    """
    Compile the molecular optimization graph.
    """
    graph = build_molecular_graph()
    return graph.compile()

