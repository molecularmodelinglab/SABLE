"""
Main entry point for running the molecular optimization workflow.
Includes state persistence and checkpointing. 
"""

import json
import pickle
from datetime import datetime
from typing import Optional, Any
from pathlib import Path

from edges.graph_builder import compile_graph
from schemas.state import WorkflowState
from schemas.errors import NodeError, ToolError


class WorkflowRunner:
    """
    Runner for the molecular optimization workflow with state persistence.
    """
    
    def __init__(self, checkpoint_dir: str = "checkpoints"):
        """
        Initialize the workflow runner.
        
        Args:
            checkpoint_dir: Directory to store checkpoints
        """
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(exist_ok=True)
        self.graph = compile_graph()
    
    def save_checkpoint(self, state: WorkflowState, checkpoint_name: Optional[str] = None) -> str:
        """
        Save a checkpoint of the current state.
        
        Args:
            state: Current workflow state
            checkpoint_name: Optional name for checkpoint
            
        Returns:
            Path to saved checkpoint
        """
        if not checkpoint_name:
            checkpoint_name = f"{state.workflow_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Ensure we don't attempt to pickle a locally wrapped logger
        if hasattr(state, "_log_wrapped") and getattr(state, "_log_wrapped", False):
            try:
                state.log = getattr(state, "_original_log")  # type: ignore
                delattr(state, "_original_log")
                delattr(state, "_log_wrapped")
            except Exception:
                pass

        checkpoint_path = self.checkpoint_dir / f"{checkpoint_name}.pkl"

        # Restore original logger if wrapped to avoid pickling local closures
        if getattr(state, "_log_wrapped", False):
            try:
                state.log = getattr(state, "_original_log")  # type: ignore
            except Exception:
                pass

        # Ensure event callback is not pickled
        try:
            if hasattr(state, "_event_callback"):
                setattr(state, "_event_callback", None)
        except Exception:
            pass

        # Temporarily remove any instance-level 'log' to prevent pickling method objects
        had_instance_log = False
        saved_instance_log = None
        if hasattr(state, "__dict__") and "log" in state.__dict__:
            had_instance_log = True
            saved_instance_log = state.__dict__.get("log")
            try:
                del state.__dict__["log"]
            except Exception:
                had_instance_log = False

        with open(checkpoint_path, 'wb') as f:
            pickle.dump(state, f)

        # Restore instance-level log if we removed it
        if had_instance_log:
            state.__dict__["log"] = saved_instance_log  # type: ignore

        # Clean wrapper markers so next save starts clean
        if hasattr(state, "_original_log"):
            try:
                delattr(state, "_original_log")
            except Exception:
                pass
        if hasattr(state, "_log_wrapped"):
            try:
                delattr(state, "_log_wrapped")
            except Exception:
                pass
        
        json_path = self.checkpoint_dir / f"{checkpoint_name}.json"
        with open(json_path, 'w') as f:
            json.dump(state, f, indent=2, default=str)
        
        print(f"Checkpoint saved: {checkpoint_path}")
        return str(checkpoint_path)
    
    def load_checkpoint(self, checkpoint_path: str) -> WorkflowState:
        """
        Load a checkpoint from file.
        
        Args:
            checkpoint_path: Path to checkpoint file
            
        Returns:
            Loaded workflow state
        """
        with open(checkpoint_path, 'rb') as f:
            state = pickle.load(f)
        
        print(f"Checkpoint loaded: {checkpoint_path}")
        return state
    
    def run(self, 
            user_prompt: str,
            checkpoint_path: Optional[str] = None,
            save_checkpoints: bool = True,
            event_callback: Optional[callable] = None) -> WorkflowState:
        """
        Run the molecular optimization workflow.
        
        Args:
            user_prompt: User's optimization request
            checkpoint_path: Optional checkpoint to resume from
            save_checkpoints: Whether to save checkpoints after each iteration
            
        Returns:
            Final workflow state
        """
        # Initialize or load state
        if checkpoint_path:
            state = self.load_checkpoint(checkpoint_path)
            print(f"Resuming from checkpoint at iteration {state.current_iteration}")
        else:
            state = WorkflowState(user_prompt=user_prompt)
            print(f"Starting new workflow: {state.workflow_id}")

        # If provided, set the state's event callback (no monkey-patching of methods)
        if event_callback is not None:
            try:
                setattr(state, "_event_callback", event_callback)  # PrivateAttr on model
            except Exception:
                pass
        
        try:
            config = {
                "recursion_limit": 50,
                "debug": True
            }
            result = self.graph.invoke(state, config=config)

            # Extract final state
            if isinstance(result, WorkflowState):
                final_state = result
            elif isinstance(result, dict) and "status" in result:
                # Some nodes might return dicts; convert minimal fields to model for consistency
                final_state = state
  
            # Save final checkpoint
            if save_checkpoints:
                self.save_checkpoint(final_state, f"{final_state.workflow_id}_final")
            
            return final_state
            
        except (NodeError, ToolError) as e:
            print(f"Error during workflow execution: {e}")
            state.status = "failed"
            state.exit_reason = getattr(e, "code", None) or str(e)
            state.log("workflow_error", getattr(e, "to_dict", lambda: {"message": str(e)})())
            if save_checkpoints:
                self.save_checkpoint(state, f"{state.workflow_id}_error")
            raise
        except Exception as e:
            print(f"Error during workflow execution: {e}")
            state.status = "failed"
            state.exit_reason = str(e)
            
            # Save error checkpoint
            if save_checkpoints:
                self.save_checkpoint(state, f"{state.workflow_id}_error")
            
            raise
    
    async def run_async(self,
                       user_prompt: str,
                       checkpoint_path: Optional[str] = None,
                       save_checkpoints: bool = True) -> WorkflowState:
        """
        Run the workflow asynchronously.
        """
        # For now, just wrap the sync version
        # In future, could use async graph execution
        return self.run(user_prompt, checkpoint_path, save_checkpoints)
    
    def export_results(self, state: WorkflowState, output_file: str = "results.json"):
        """
        Export optimization results to a JSON file.
        
        Args:
            state: Final workflow state
            output_file: Output file path
        """
        results = {
            "workflow_id": state.workflow_id,
            "user_prompt": state.user_prompt,
            "status": state.status,
            "exit_reason": state.exit_reason,
            "summary": state.summary,
            "configuration": {
                "targets": [t.model_dump() for t in state.targets],
                "molecule_source": state.molecule_source,
                "max_iterations": state.max_iterations,
                "bo_config": state.bo_config.model_dump() if state.bo_config else None,
            },
            "results": {
                "total_iterations": state.current_iteration,
                "molecules_tested": len(state.experimental_results),
                "best_molecules": [
                    {
                        "smiles": smiles,
                        "score": score,
                        "properties": next(
                            (r.properties for r in state.experimental_results if r.smiles == smiles),
                            {},
                        ),
                    }
                    for smiles, score in state.best_molecules[:10]
                ],
            },
            "experimental_data": [r.model_dump() for r in state.experimental_results],
            "logs": state.logs,
        }

        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2, default=str)

        print(f"Results exported to: {output_file}")


def main():
    """
    Main function for command-line usage.
    """
    import argparse
    
    parser = argparse.ArgumentParser(description="Run molecular optimization workflow")
    parser.add_argument("prompt", nargs="?", help="Optimization prompt")
    parser.add_argument("--checkpoint", help="Resume from checkpoint file")
    parser.add_argument("--no-checkpoints", action="store_true", 
                       help="Disable checkpoint saving")
    parser.add_argument("--output", default="results.json",
                       help="Output file for results (default: results.json)")
    parser.add_argument("--example", action="store_true",
                       help="Run with example prompt")
    
    args = parser.parse_args()
    
    # Handle example mode
    if args.example:
        prompt = "Optimize aspirin for better QED. Enumerate 50 analogs and run 5 iterations of optimization."
        print(f"Running example: {prompt}")
    elif args.prompt:
        prompt = args.prompt
    elif not args.checkpoint:
        print("Error: Please provide a prompt or use --checkpoint to resume")
        parser.print_help()
        return
    else:
        prompt = None  # Will be loaded from checkpoint
    
    runner = WorkflowRunner()
    
    try:
        final_state = runner.run(
            user_prompt=prompt,
            checkpoint_path=args.checkpoint,
            save_checkpoints=not args.no_checkpoints
        )
        
        runner.export_results(final_state, args.output)
        
        print("\n" + "="*50)
        print("Workflow completed successfully!")
        print(f"Final status: {final_state.status}")
        if final_state.best_molecules:
            best_mol, best_score = final_state.best_molecules[0]
            print(f"Best molecule score: {best_score:.4f}")
            print(f"Best molecule SMILES: {best_mol}")
        
    except Exception as e:
        print(f"\nWorkflow failed with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()