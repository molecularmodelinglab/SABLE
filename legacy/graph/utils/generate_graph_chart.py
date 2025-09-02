"""
molecular_graph.py
==================

Complete molecular optimization workflow graph definition.
This file contains the compiled LangGraph for molecular optimization.
"""

from langgraph import StateGraph, START, END

# Import all nodes based on your file structure
from nodes.extract_arguments import extract_arguments_node
from nodes.setup import setup_node
from nodes.generate_molecule import generate_molecule_node
from nodes.enumerate_molecules import enumerate_molecules_node
from nodes.decide_library import decide_library_node
from nodes.validate_molecule import validate_molecule_node
from nodes.bo_iteration import bo_iteration_node
from nodes.llm_experiment import llm_experiment_node
from nodes.record_experimental_results import record_experimental_results_node
from nodes.check_exit_conditions import check_exit_conditions_node
from nodes.summarize_results import summarize_results_node
from nodes.BOnodes.recording import record_to_json_node

# Create the graph
molecular_graph = StateGraph(dict)

# Add all nodes to the graph
molecular_graph.add_node("extract_arguments", extract_arguments_node)
molecular_graph.add_node("setup", setup_node)
molecular_graph.add_node("generate_molecule", generate_molecule_node)
molecular_graph.add_node("enumerate_molecules", enumerate_molecules_node)
molecular_graph.add_node("decide_library", decide_library_node)
molecular_graph.add_node("validate_molecule", validate_molecule_node)
molecular_graph.add_node("bo_iteration", bo_iteration_node)
molecular_graph.add_node("llm_experiment", llm_experiment_node)
molecular_graph.add_node("record_experimental_results", record_experimental_results_node)
molecular_graph.add_node("check_exit_conditions", check_exit_conditions_node)
molecular_graph.add_node("summarize_results", summarize_results_node)
molecular_graph.add_node("record_to_json", record_to_json_node)

# Routing functions
def route_molecule_source(state):
    """
    Route based on the molecule source determined during setup.
    """
    tracker = state['tracker']
    
    # Check what molecule source was determined
    if tracker.starting_molecules:  # Has starting molecules to validate
        return "validate_given"
    else:  # Default to generation
        return "generate"

def route_library_strategy(state):
    """
    Route based on the library strategy decision.
    """
    tracker = state['tracker']
    
    # Check the decision made by decide_library_node
    library_strategy = tracker.metadata.get("library_strategy")
    
    if library_strategy == "external_library":
        return "external_library"
    else:  # Default to enumeration
        return "enumerate"

def route_exit_decision(state):
    """
    Route based on whether exit conditions are met.
    """
    tracker = state['tracker']
    
    # Check if exit conditions were met
    if tracker.is_complete or tracker.exit_condition_met:
        return "exit"
    else:
        # Increment iteration for next BO round
        tracker.current_iteration += 1
        return "continue"

# Add edges - Start with entry point
molecular_graph.add_edge(START, "extract_arguments")

# Simple edges - direct flow
molecular_graph.add_edge("extract_arguments", "setup")
molecular_graph.add_edge("validate_molecule", "decide_library")
molecular_graph.add_edge("generate_molecule", "decide_library")
molecular_graph.add_edge("enumerate_molecules", "bo_iteration")
molecular_graph.add_edge("bo_iteration", "llm_experiment")
molecular_graph.add_edge("llm_experiment", "record_experimental_results")
molecular_graph.add_edge("record_experimental_results", "check_exit_conditions")
molecular_graph.add_edge("summarize_results", "record_to_json")
molecular_graph.add_edge("record_to_json", END)

# Conditional edges - decision points
molecular_graph.add_conditional_edges(
    "setup",
    route_molecule_source,
    {
        "generate": "generate_molecule",
        "validate_given": "validate_molecule"
    }
)

molecular_graph.add_conditional_edges(
    "decide_library",
    route_library_strategy,
    {
        "enumerate": "enumerate_molecules",
        "external_library": "bo_iteration"  # Connect external library directly to BO
    }
)

molecular_graph.add_conditional_edges(
    "check_exit_conditions",
    route_exit_decision,
    {
        "continue": "bo_iteration",
        "exit": "summarize_results"
    }
)

# Compile the graph
compiled_molecular_graph = molecular_graph.compile()

# Optional: Add debugging/visualization helper
def visualize_graph():
    """Helper function to visualize the graph structure"""
    try:
        # This requires graphviz to be installed
        return compiled_molecular_graph.get_graph().draw_mermaid()
    except:
        return "Graph visualization not available (install graphviz for visual representation)"

if __name__ == "__main__":
    print("Molecular optimization graph compiled successfully!")
    print(f"Graph has {len(molecular_graph.nodes)} nodes")
    print("Available nodes:", list(molecular_graph.nodes.keys()))
    
    # Try to show graph structure
    try:
        print("\nGraph structure:")
        print(visualize_graph())
    except Exception as e:
        print(f"Could not visualize graph: {e}")