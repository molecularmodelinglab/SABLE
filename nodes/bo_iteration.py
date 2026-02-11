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

    # Require BayBE to be available
    if not BAYBE_AVAILABLE:
        emit_event(state, kind="baybe_unavailable", node="bo_iteration", severity="error")
        raise NodeError(
            "BayBE library is not available. Bayesian Optimization requires BayBE to be installed.",
            node="bo_iteration",
            code="BAYBE_UNAVAILABLE"
        )

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
                    # Gracefully end the campaign instead of raising an error
                    emit_event(state, kind="no_bo_recommendations", node="bo_iteration", severity="warning")
                    state.status = "completed"
                    state.exit_reason = "NO_RECOMMENDATIONS"
                    state.current_bo_recommendations = []
                    state.log("bo_iteration_no_recommendations", {
                        "message": "BO returned no valid recommendations. Ending campaign gracefully.",
                        "iteration": state.current_iteration,
                        "total_tested": len(state.experimental_results)
                    })
                    print(f"⚠️  No valid BO recommendations available. Ending campaign gracefully.")
                    print(f"   Total molecules tested: {len(state.experimental_results)}")
                    return state

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
        except NodeError as e:
            # If it's a NodeError from within our try block, re-raise it
            # (This shouldn't happen anymore since we handle NO_RECOMMENDATIONS gracefully above)
            raise
        except Exception as e:
            emit_event(state, kind="bo_exception", node="bo_iteration", severity="error", data={"error": str(e)})
            raise NodeError(
                f"Bayesian Optimization failed: {str(e)}",
                node="bo_iteration",
                code="BO_EXCEPTION"
            )

    print(f"🔍 EXITING NODE: {bo_iteration_node.__name__}")
    print(f"   - New iteration: {state.current_iteration}")
    print(f"   - New status: {state.status}")
    print(f"   - Should continue: {state.should_continue()}")

    return state