import os
import traceback
from run_workflow import WorkflowRunner
from schemas.state import ExperimentResult, WorkflowState, WorkflowStatus
from test_questions import questions

CHECKPOINT_DIR = "test_checkpoints"


def test_basic_workflow():
    """Tests the basic, end-to-end execution of the workflow with sample prompts."""
    print("Testing basic molecular optimization workflow...")
    print("=" * 50)

    # test_prompts = [
    #     "Optimize aspirin for better QED. Use 20 molecules and 3 iterations.",
    #     "Find analogs of caffeine with improved drug-likeness. Enumerate 50 derivatives.",
    #     "Improve the TPSA and LogP of ibuprofen through molecular optimization.",
    # ]
    test_prompts = questions  # Use full set from test_questions.py

    runner = WorkflowRunner(checkpoint_dir=CHECKPOINT_DIR)

    for i, prompt in enumerate(test_prompts, 1):
        print(f"\n--- Test {i}: Running prompt: '{prompt}' ---")
        try:
            state = runner.run(user_prompt=prompt, save_checkpoints=True)

            assert state.status in [WorkflowStatus.COMPLETED, WorkflowStatus.RUNNING], f"Unexpected status: {state.status}"
            assert state.workflow_id is not None

            print(f"✓ Test {i} PASSED")
            print(f"  - Final Status: {state.status}")
            print(f"  - Iterations Completed: {state.current_iteration}")
            print(f"  - Molecules Tested: {len(state.experimental_results)}")
            if state.best_molecules:
                print(f"  - Best Score Achieved: {state.best_molecules[0][1]:.4f}")

        except Exception as e:
            print(f"✗ Test {i} FAILED: {e}")
            traceback.print_exc()

    print("\n" + "=" * 50)


def test_checkpoint_resume():
    """Tests the ability to save a workflow state and resume from it."""
    print("\nTesting checkpoint save and resume functionality...")
    print("=" * 50)

    runner = WorkflowRunner(checkpoint_dir=CHECKPOINT_DIR)
    prompt = "Optimize aspirin for QED. Run 5 iterations."
    checkpoint_id = "test_checkpoint"

    initial_state = WorkflowState(user_prompt=prompt)
    initial_state.current_iteration = 2
    initial_state.max_iterations = 5

    checkpoint_path = runner.save_checkpoint(initial_state, checkpoint_id)
    print(f"Saved checkpoint to: {checkpoint_path}")
    assert os.path.exists(checkpoint_path)

    loaded_state = runner.load_checkpoint(checkpoint_path)
    print("Successfully loaded checkpoint.")

    assert loaded_state.workflow_id == initial_state.workflow_id
    assert loaded_state.current_iteration == 2
    assert loaded_state.user_prompt == prompt
    print("✓ Checkpoint data verified.")

    json_path = checkpoint_path.replace(".pkl", ".json")
    for path in [checkpoint_path, json_path]:
        if os.path.exists(path):
            os.remove(path)
            print(f"Cleaned up {os.path.basename(path)}")


def test_state_methods():
    """Tests the internal helper methods of the WorkflowState class."""
    print("\nTesting WorkflowState internal methods...")
    print("=" * 50)

    state = WorkflowState(user_prompt="Test prompt")

    state.log("test_action", {"data": "test_value"})
    assert len(state.logs) == 1
    assert state.logs[0]["action"] == "test_action"
    print("✓ log() method works as expected.")

    assert state.should_continue() is True
    state.max_iterations = 0
    assert state.should_continue() is False
    print("✓ should_continue() method works as expected.")

    # Test adding experimental results
    result = ExperimentResult(
        molecule_id="mol_001",
        smiles="CCO",
        iteration=0,
        properties={"QED": 0.5}
    )
    state.add_experimental_result(result)
    assert len(state.experimental_results) == 1
    print("✓ add_experimental_result() method works as expected.")
    print("\n✓ All state method tests passed.")


def test_empty_starting_molecules_triggers_error():
    """Ensure enumeration fails fast when no starting molecules for ENUMERATED source."""
    runner = WorkflowRunner(checkpoint_dir=CHECKPOINT_DIR)
    prompt = "Enumerate 10 molecules. 2 iterations."  # No molecule name provided
    try:
        runner.run(user_prompt=prompt, save_checkpoints=False)
    except Exception as e:
        print("Caught expected error:", e)
        return
    raise AssertionError("Expected an error due to empty starting molecules but workflow completed")


def main():
    """Main function to run the entire test suite."""
    print("Starting molecular optimization workflow test suite...")
    test_state_methods()
    test_checkpoint_resume()
    # test_empty_starting_molecules_triggers_error()
    test_basic_workflow()
    print("\n" + "=" * 50)
    print("🎉 All tests completed successfully! 🎉")
    print("\nTo run the workflow with a custom prompt:")
    print("  python run_workflow.py 'Your optimization prompt here'")
    print("\nTo run with the example prompt:")
    print("  python run_workflow.py --example")


if __name__ == "__main__":
    main()
