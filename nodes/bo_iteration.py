"""
Bayesian Optimization iteration node.
"""

from typing import Dict, Any, List
import json
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas.state import WorkflowState
from tools.bayesopt_tool import BayesianOptimizationTool, BAYBE_AVAILABLE


def bo_iteration_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Run a Bayesian Optimization iteration to select next molecules.
    """
    print(f"🔍 ENTERING NODE: {bo_iteration_node.__name__}")
    print(f"   - Current iteration: {state.current_iteration}")
    print(f"   - Max iterations: {state.max_iterations}")
    print(f"   - Status: {state.status}")
    print(f"   - Should continue: {state.should_continue()}")


    state.log("bo_iteration_started", {
        "iteration": state.current_iteration,
        "search_space_size": len(state.search_space)
    })
    
    if not state.search_space:
        state.log("bo_iteration_error", "No search space available")
        return state
    
    if not BAYBE_AVAILABLE:
        import random
        batch_size = state.bo_config.batch_size if state.bo_config else 5
        available_ids = list(state.search_space.keys())
        
        # Filter out already tested molecules
        tested_smiles = {r.smiles for r in state.experimental_results}
        available_ids = [
            mol_id for mol_id in available_ids 
            if state.search_space[mol_id] not in tested_smiles
        ]
        
        selected_ids = random.sample(
            available_ids, 
            min(batch_size, len(available_ids))
        )
        
        state.current_bo_recommendations = selected_ids
        state.log("bo_iteration_fallback", {
            "method": "random_selection",
            "selected_count": len(selected_ids)
        })
        
    else:
        bo_tool = BayesianOptimizationTool()
        
        targets = []
        for target in state.targets:
            target_dict = {
                "name": target.name,
                "mode": target.mode,
                "bounds": target.bounds if target.bounds else (0.0, 1.0),
                "transformation": target.transformation
            }
            if len(state.targets) > 1:
                target_dict["weight"] = target.weight
            targets.append(target_dict)
        
        measurement_data = None
        if state.experimental_results:
            measurement_data = []
            for result in state.experimental_results:
                mol_id = None
                for mid, smiles in state.search_space.items():
                    if smiles == result.smiles:
                        mol_id = mid
                        break
                
                if mol_id:
                    measurement = {
                        "Molecule_ID": mol_id,
                        **result.properties
                    }
                    measurement_data.append(measurement)
        
        try:
            memory = {"search_space": state.search_space}
            
            result = bo_tool._run(
                targets=json.dumps(targets),
                batch_size=state.bo_config.batch_size,
                encoding=state.bo_config.encoding,
                measurement_data=measurement_data,
                memory=memory
            )
            
            if isinstance(result, list):
                state.current_bo_recommendations = result
                
                bo_round = {
                    "iteration": state.current_iteration,
                    "recommendations": result,
                    "measurement_count": len(measurement_data) if measurement_data else 0
                }
                state.bo_rounds.append(bo_round)
                
                state.log("bo_iteration_completed", {
                    "recommended_ids": result,
                    "recommendation_count": len(result)
                })
            else:
                state.log("bo_iteration_error", f"Unexpected result from BO tool: {result}")
                
        except Exception as e:
            print(f"❌ ERROR in bo_iteration_node: {e}")
            state.log("bo_iteration_error", str(e))
            # Fallback to random selection
            import random
            batch_size = state.bo_config.batch_size if state.bo_config else 5
            available_ids = list(state.search_space.keys())
            selected_ids = random.sample(
                available_ids, 
                min(batch_size, len(available_ids))
            )
            state.current_bo_recommendations = selected_ids

    print(f"🔍 EXITING NODE: {bo_iteration_node.__name__}")
    print(f"   - New iteration: {state.current_iteration}")
    print(f"   - New status: {state.status}")
    print(f"   - Should continue: {state.should_continue()}")
    
    return state