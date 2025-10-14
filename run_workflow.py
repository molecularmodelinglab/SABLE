"""
Main entry point for running the molecular optimization workflow.
Includes state persistence and checkpointing. 
"""

import json
import pickle
from datetime import datetime
from typing import Optional
from pathlib import Path

from edges.graph_builder import compile_graph
from schemas.state import WorkflowState
from schemas.errors import NodeError, ToolError
from utils.llm_factory import get_llm_client


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
    
    def save_checkpoint(self, state, checkpoint_name: Optional[str] = None) -> str:
        """
        Save a checkpoint of the current state.
        
        Args:
            state: Current workflow state (WorkflowState object or dict)
            checkpoint_name: Optional name for checkpoint
            
        Returns:
            Path to saved checkpoint
        """
        # Handle both WorkflowState objects and dicts
        if isinstance(state, dict):
            workflow_id = state.get('workflow_id', 'unknown')
            state_dict = state
        else:
            workflow_id = state.workflow_id
            state_dict = state
        
        if not checkpoint_name:
            checkpoint_name = f"{workflow_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        checkpoint_path = self.checkpoint_dir / f"{checkpoint_name}.pkl"
        
        # Temporarily remove llm_client before pickling (it's not serializable)
        llm_client_backup = None
        if isinstance(state, dict):
            llm_client_backup = state_dict.pop('llm_client', None)
        else:
            llm_client_backup = getattr(state, 'llm_client', None)
            state.llm_client = None
        
        try:
            with open(checkpoint_path, 'wb') as f:
                pickle.dump(state_dict, f)
        finally:
            # Restore llm_client
            if isinstance(state, dict):
                if llm_client_backup is not None:
                    state_dict['llm_client'] = llm_client_backup
            else:
                state.llm_client = llm_client_backup
        
        json_path = self.checkpoint_dir / f"{checkpoint_name}.json"
        with open(json_path, 'w') as f:
            # Create a JSON-safe copy without llm_client
            json_state = dict(state_dict) if isinstance(state_dict, dict) else state_dict.model_dump()
            json_state.pop('llm_client', None)
            json.dump(json_state, f, indent=2, default=str)
        
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
        
        # Reinitialize LLM client after loading (it wasn't serialized)
        print("Reinitializing LLM client...")
        state.llm_client = get_llm_client()
        if state.llm_client:
            print(f"✓ LLM client reinitialized successfully")
        else:
            print("⚠ LLM client not available - will use rule-based extraction only")
        
        print(f"Checkpoint loaded: {checkpoint_path}")
        return state
    
    def run(self, 
            user_prompt: str,
            checkpoint_path: Optional[str] = None,
            save_checkpoints: bool = True) -> WorkflowState:
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
            
            # Initialize LLM client for argument extraction
            print("Initializing LLM client...")
            state.llm_client = get_llm_client()
            if state.llm_client:
                print(f"✓ LLM client initialized successfully")
            else:
                print("⚠ LLM client not available - will use rule-based extraction only")
        
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
                final_state = result
  
            # Save final checkpoint
            if save_checkpoints:
                self.save_checkpoint(final_state, f"{final_state['workflow_id']}_final")
            
            return final_state
            
        except (NodeError, ToolError) as e:
            print(f"\n{'='*70}")
            print(f"❌ WORKFLOW ERROR")
            print(f"{'='*70}")
            print(f"Error Type: {type(e).__name__}")
            print(f"Error Message: {e}")
            print(f"Error Code: {getattr(e, 'code', 'N/A')}")
            
            # Print workflow context for debugging
            print(f"\n📋 Workflow Context at Failure:")
            print(f"   Workflow ID: {state.workflow_id}")
            print(f"   Status: {state.status}")
            print(f"   Current Iteration: {state.current_iteration}/{state.max_iterations}")
            
            if state.parsed_arguments:
                print(f"\n🛠️  Parsed Arguments:")
                for key, value in state.parsed_arguments.items():
                    if key not in ['llm_confidence', 'extraction_quality', 'confidence_score', 'extraction_method']:
                        print(f"   {key}: {value}")
            
            if state.starting_molecules:
                print(f"\n🧪 Starting Molecules ({len(state.starting_molecules)}):")
                for i, smiles in enumerate(state.starting_molecules[:3], 1):
                    print(f"   {i}. {smiles}")
                if len(state.starting_molecules) > 3:
                    print(f"   ... and {len(state.starting_molecules) - 3} more")
            
            if state.targets:
                print(f"\n🎯 Target Properties ({len(state.targets)}):")
                for target in state.targets:
                    print(f"   - {target.name}: {target.mode.value}, weight={target.weight:.3f}, bounds={target.bounds}")
            
            if state.search_space:
                print(f"\n🔬 Search Space: {len(state.search_space)} molecules")
            
            if state.experimental_results:
                print(f"\n📊 Experimental Results: {len(state.experimental_results)} evaluations")
            
            print(f"\n💾 Error checkpoint will be saved to: {state.workflow_id}_error.pkl")
            print(f"{'='*70}\n")
            
            state.status = "failed"
            state.exit_reason = getattr(e, "code", None) or str(e)
            
            # Enhanced error logging with full context
            error_context = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_code": getattr(e, "code", None),
                "workflow_id": state.workflow_id,
                "current_iteration": state.current_iteration,
                "max_iterations": state.max_iterations,
                "status": state.status,
                "parsed_arguments": state.parsed_arguments,
                "starting_molecules": state.starting_molecules,
                "targets": [t.model_dump() for t in state.targets] if state.targets else [],
                "molecule_source": state.molecule_source,
                "search_space_size": len(state.search_space),
                "results_count": len(state.experimental_results)
            }
            
            state.log("workflow_error", error_context)
            
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
            "workflow_id": state["workflow_id"],
            "user_prompt": state["user_prompt"],
            "status": state["status"],
            "exit_reason": state["exit_reason"],
            "summary": state["summary"],
            "configuration": {
                "targets": [t.model_dump() for t in state["targets"]],
                "molecule_source": state["molecule_source"],
                "max_iterations": state["max_iterations"],
                "bo_config": state["bo_config"].model_dump() if state["bo_config"] else None
            },
            "results": {
                "total_iterations": state["current_iteration"],
                "molecules_tested": len(state["experimental_results"]),
                "best_molecules": [
                    {
                        "smiles": smiles,
                        "score": score,
                        "properties": next(
                            (r.properties for r in state["experimental_results"] if r.smiles == smiles),
                            {}
                        )
                    }
                    for smiles, score in state["best_molecules"][:10]
                ]
            },
            "experimental_data": [r.model_dump() for r in state["experimental_results"]],
            "logs": state["logs"]
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