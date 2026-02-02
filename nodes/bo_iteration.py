"""
Bayesian Optimization iteration node.
"""

from typing import Dict, Any, List
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas.state import WorkflowState
from schemas.errors import NodeError, ToolError
from utils.telemetry import emit_event
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
        emit_event(state, kind="no_search_space", node="bo_iteration", severity="error")
        raise NodeError("No search space available for BO iteration", node="bo_iteration", code="EMPTY_SEARCH_SPACE")

    # Fallback path if BayBE is unavailable
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

        if not available_ids:
            raise NodeError("No available molecules remain for selection", node="bo_iteration", code="NO_CANDIDATES")

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

        measurement_data: List[Dict[str, Any]] = []
        if state.experimental_results:
            for result in state.experimental_results:
                mol_id = None
                for mid, smiles in state.search_space.items():
                    if smiles == result.smiles:
                        mol_id = mid
                        break
                if mol_id:
                    measurement = {"Molecule_ID": mol_id}
                    measurement.update(result.properties)
                    measurement_data.append(measurement)

        try:
            result = bo_tool._run(
                targets=state.targets,
                batch_size=state.bo_config.batch_size if state.bo_config else 5,
                encoding=state.bo_config.encoding if state.bo_config else os.getenv("MOLECULAR_FP", "MORDRED"),
                measurement_data=measurement_data if measurement_data else None,
                search_space=state.search_space,
            )

            if isinstance(result, str):
                emit_event(state, kind="bo_tool_error", node="bo_iteration", tool="BayesianOptimizer", severity="error", data={"message": result})
                raise ToolError(result, node="bo_iteration", tool="BayesianOptimizer", code="BO_FAILED")

            if isinstance(result, list):
                # Filter to valid IDs present in search_space and not yet tested
                tested_smiles = {r.smiles for r in state.experimental_results}
                valid_ids = [mid for mid in result if mid in state.search_space and state.search_space[mid] not in tested_smiles]
                if not valid_ids:
                    emit_event(state, kind="no_bo_recommendations", node="bo_iteration", severity="error")
                    raise NodeError("BO returned no valid recommendations", node="bo_iteration", code="NO_RECOMMENDATIONS")

                state.current_bo_recommendations = valid_ids

                bo_round = {
                    "iteration": state.current_iteration,
                    "recommendations": valid_ids,
                    "measurement_count": len(measurement_data),
                }
                state.bo_rounds.append(bo_round)

                state.log("bo_iteration_completed", {
                    "recommended_ids": valid_ids,
                    "recommendation_count": len(valid_ids)
                })
            else:
                # Unexpected type
                raise ToolError(
                    f"Unexpected result type from BO tool: {type(result)}",
                    node="bo_iteration",
                    tool="BayesianOptimizer",
                    code="BO_BAD_RESULT_TYPE",
                )
        except ToolError:
            raise
        except Exception as e:
            # Last-resort fallback: random selection
            emit_event(state, kind="bo_exception", node="bo_iteration", severity="error", data={"error": str(e)})
            import random
            batch_size = state.bo_config.batch_size if state.bo_config else 5
            available_ids = list(state.search_space.keys())
            tested_smiles = {r.smiles for r in state.experimental_results}
            available_ids = [mid for mid in available_ids if state.search_space[mid] not in tested_smiles]
            if not available_ids:
                raise NodeError("No available molecules remain after exception fallback", node="bo_iteration", code="NO_CANDIDATES")
            selected_ids = random.sample(available_ids, min(batch_size, len(available_ids)))
            state.current_bo_recommendations = selected_ids

    print(f"🔍 EXITING NODE: {bo_iteration_node.__name__}")
    print(f"   - New iteration: {state.current_iteration}")
    print(f"   - New status: {state.status}")
    print(f"   - Should continue: {state.should_continue()}")

    return state