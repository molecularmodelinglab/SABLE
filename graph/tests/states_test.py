"""
Test Molecular Workflow - Updated for Your File Structure
========================================================

This script tests the complete molecular optimization workflow with dummy data
using your actual file organization.
"""

from graph.state import StateTracker  # Adjust import based on your actual file structure

# Import based on your actual file structure
from nodes.extract_arguments import extract_arguments_node
from nodes.setup import setup_node  
from nodes.generate_molecule import generate_molecule_node
from nodes.enumerate_molecules import enumerate_molecules_node
from nodes.decide_library import decide_library_node
from nodes.validate_molecule import validate_molecule_node
from nodes.bo_iteration import bo_iteration_node
from nodes.llm_experiment import llm_experiment_node
from nodes.record_experimental_results import record_experimental_results_node
from nodes.check_exit_conditionsonditions_node
from nodes.summarize_results import summarize_results_node
from nodes.BOnodes.recording import summarize_results_node

# Import your compiled graph (you'll need to create this file)
# Assuming you saved your graph definition in molecular_graph.py
try:
    from graph.molecular_graph import compiled_molecular_graph
except ImportError:
    print("Error: molecular_graph.py not found. Create this file with your graph definition.")
    print("Copy your graph code into a file named 'molecular_graph.py'")
    exit(1)

def test_workflow_basic():
    """Basic test to verify workflow runs without errors"""
    print("=" * 50)
    print("Testing: Basic Workflow Execution")
    print("=" * 50)
    
    # Create tracker with simple prompt
    tracker = StateTracker(
        research_id="test_basic_001",
        prompt="Find molecules with good QED, run for 2 iterations"
    )
    
    # Initial state
    initial_state = {
        "tracker": tracker
    }
    
    print(f"Starting basic workflow test...")
    print(f"Research ID: {tracker.research_id}")
    print(f"Prompt: {tracker.original_prompt}")
    
    try:
        # Run the workflow
        final_state = compiled_molecular_graph.invoke(initial_state)
        
        # Check results
        final_tracker = final_state["tracker"]
        
        print(f"\nWorkflow Results:")
        print(f"- Completed: {'Yes' if final_tracker.is_complete else 'No'}")
        print(f"- Exit reason: {final_tracker.exit_condition_met or 'Still running'}")
        print(f"- Iterations: {final_tracker.current_iteration}")
        print(f"- Total molecules: {len(final_tracker.molecules)}")
        print(f"- Completed experiments: {len(final_tracker.completed_experiments)}")
        print(f"- Failed experiments: {len([e for e in final_tracker.experiments.values() if e.status.value == 'failed'])}")
        print(f"- Log entries: {len(final_tracker.logs)}")
        
        # Show recent actions
        if final_tracker.logs:
            print(f"\nRecent workflow actions:")
            for log in final_tracker.logs[-5:]:
                print(f"  - {log['action']}")
        
        return True
        
    except Exception as e:
        print(f"Workflow failed with error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_workflow_with_starting_molecule():
    """Test with a starting molecule to verify validation path"""
    print("\n" + "=" * 50)
    print("Testing: Workflow with Starting Molecule")
    print("=" * 50)
    
    # Create tracker with a starting molecule in the prompt
    tracker = StateTracker(
        research_id="test_starting_mol_001",
        prompt="Optimize this molecule CC(=O)NC1=CC=CC=C1 for better solubility"
    )
    
    initial_state = {
        "tracker": tracker
    }
    
    print(f"Testing workflow with starting molecule...")
    
    try:
        final_state = compiled_molecular_graph.invoke(initial_state)
        final_tracker = final_state["tracker"]
        
        print(f"\nStarting Molecule Test Results:")
        print(f"- Starting molecules found: {len(final_tracker.starting_molecules)}")
        print(f"- Molecules validated: {len([m for m in final_tracker.molecules.values() if m.metadata.get('validated')])}")
        print(f"- Workflow completed: {'Yes' if final_tracker.is_complete else 'No'}")
        
        return True
        
    except Exception as e:
        print(f"Starting molecule test failed: {str(e)}")
        return False

def test_individual_nodes():
    """Test individual nodes to identify which ones work"""
    print("\n" + "=" * 50)
    print("Testing: Individual Node Functions")
    print("=" * 50)
    
    # Create a tracker for testing
    tracker = StateTracker(
        research_id="test_nodes_001",
        prompt="Test individual node functionality"
    )
    
    state = {"tracker": tracker}
    
    # List of nodes to test
    nodes_to_test = [
        ("Extract Arguments", extract_arguments_node),
        ("Setup", setup_node),
        ("Generate Molecule", generate_molecule_node),
        ("Enumerate Molecules", enumerate_molecules_node),
        ("Decide Library", decide_library_node),
        ("Validate Molecules", validate_molecule_node),
        ("BO Iteration", bo_iteration_node),
        ("LLM Experiment", llm_experiment_node),
        ("Record Results", record_experimental_results_node),
        ("Check Exit", check_exit_conditions_node),
        ("Summarize", summarize_results_node),
        ("Record JSON", record_to_json_node)
    ]
    
    results = []
    
    for node_name, node_function in nodes_to_test:
        try:
            print(f"Testing {node_name}...", end=" ")
            
            # Create fresh state for each test
            test_state = {"tracker": StateTracker(f"test_{node_name.lower().replace(' ', '_')}", "test prompt")}
            
            # Run the node
            result_state = node_function(test_state)
            
            # Basic validation
            if result_state and "tracker" in result_state:
                print("PASS")
                results.append(True)
            else:
                print("FAIL - Invalid return")
                results.append(False)
                
        except Exception as e:
            print(f"FAIL - {str(e)}")
            results.append(False)
    
    passed = sum(results)
    total = len(results)
    
    print(f"\nIndividual Node Test Results: {passed}/{total} passed")
    
    return passed == total

def debug_workflow_step_by_step():
    """Debug workflow by running one step at a time"""
    print("\n" + "=" * 50)
    print("Debug: Step-by-Step Workflow Execution")
    print("=" * 50)
    
    tracker = StateTracker(
        research_id="debug_001",
        prompt="Debug workflow step by step"
    )
    
    initial_state = {"tracker": tracker}
    
    try:
        print("Starting step-by-step execution...")
        step_count = 0
        
        for step in compiled_molecular_graph.stream(initial_state):
            step_count += 1
            node_name = list(step.keys())[0]
            step_state = list(step.values())[0]
            
            print(f"\nStep {step_count}: {node_name}")
            print(f"  Tracker status: {step_state['tracker'].current_iteration} iterations")
            print(f"  Molecules: {len(step_state['tracker'].molecules)}")
            print(f"  Last log: {step_state['tracker'].logs[-1]['action'] if step_state['tracker'].logs else 'None'}")
            
            # Stop after reasonable number of steps for debugging
            if step_count >= 15:
                print("  (Stopping debug after 15 steps)")
                break
                
        return True
        
    except Exception as e:
        print(f"Debug failed at step {step_count}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def run_all_tests():
    """Run all available tests"""
    print("Testing Molecular Workflow")
    print("Using file structure:")
    print("  nodes/extractArgs.py")
    print("  nodes/BOnodes/moleculeSelection.py")
    print("  etc.")
    print("=" * 60)
    
    results = []
    
    # Test individual nodes first (helps identify issues)
    print("Step 1: Testing individual nodes...")
    results.append(test_individual_nodes())
    
    if results[-1]:  # Only continue if individual nodes work
        print("\nStep 2: Testing basic workflow...")
        results.append(test_workflow_basic())
        
        print("\nStep 3: Testing with starting molecule...")
        results.append(test_workflow_with_starting_molecule())
        
        print("\nStep 4: Debug step-by-step...")
        results.append(debug_workflow_step_by_step())
    else:
        print("Skipping workflow tests due to node failures")
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    
    test_names = ["Individual Nodes", "Basic Workflow", "Starting Molecule", "Step-by-Step Debug"]
    
    for i, (name, result) in enumerate(zip(test_names[:len(results)], results)):
        status = "PASS" if result else "FAIL"
        print(f"{name}: {status}")
    
    passed = sum(results)
    total = len(results)
    
    print(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        print("All tests passed! Your workflow is ready to use.")
    else:
        print("Some tests failed. Check individual node implementations.")
    
    return passed == total

if __name__ == "__main__":
    # Create required directories
    import os
    os.makedirs("outputs", exist_ok=True)
    os.makedirs("results", exist_ok=True)
    
    # Run tests
    success = run_all_tests()
    
    if not success:
        print("\nTroubleshooting Tips:")
        print("1. Make sure all node files exist and have the correct function names")
        print("2. Check that StateTracker.py is in the root directory") 
        print("3. Verify your molecular_graph.py file exists and exports compiled_molecular_graph")
        print("4. Look at individual node test results to see which ones are failing")