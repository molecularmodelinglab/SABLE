"""
Bayesian Optimization iteration node.
"""

from typing import Dict, Any, List
import time
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas.state import WorkflowState, TargetProperty
from schemas.errors import NodeError, ToolError
from schemas.tool_registry import ToolKind, ToolRunRecord, ToolSpec
from schemas.tool_schemas import BORecommendationRequest, BORecommendationResult
from utils.telemetry import emit_event
from tools.registry import ToolRegistry, get_tool_registry

def bo_iteration_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Run a Bayesian Optimization iteration to select next molecules.
    """
    print(f"🔍 ENTERING NODE: {bo_iteration_node.__name__}")
    print(f"   - Current iteration: {state.current_iteration}")
    print(f"   - Max iterations: {state.max_iterations}")
    print(f"   - Status: {state.status}")
    print(f"   - Should continue: {state.should_continue()}")

    node_started_at = time.perf_counter()
    state.profiling.setdefault("iteration_timers", {})
    state.profiling["iteration_timers"].setdefault(str(state.current_iteration), {
        "started_at": time.time()
    })

    state.log("bo_iteration_started", {
        "iteration": state.current_iteration,
        "search_space_size": len(state.search_space)
    })

    if not state.search_space:
        emit_event(state, kind="no_search_space", node="bo_iteration", severity="error")
        raise NodeError("No search space available for BO iteration", node="bo_iteration", code="EMPTY_SEARCH_SPACE")

    registry = get_tool_registry()
    optimizer_spec = _select_optimizer_spec(state, registry)
    state.record_tool_selection(
        registry.selection_for(
            stage=ToolKind.OPTIMIZER,
            spec=optimizer_spec,
            reason="Selected by optimizer strategy and batch recommendation capability.",
        )
    )

    measurement_data = _measurement_data_from_state(state)
    request = BORecommendationRequest(
        search_space=state.search_space,
        targets=[_target_payload(target) for target in state.targets],
        measurements=measurement_data or None,
        batch_size=state.bo_config.batch_size if state.bo_config else 5,
        encoding=state.bo_config.encoding if state.bo_config else os.getenv("MOLECULAR_FP", "MORDRED"),
        optimizer_strategy=_optimizer_strategy(state),
    )

    try:
        optimizer_tool = registry.create(optimizer_spec.id)
        raw_result = _run_optimizer_tool(optimizer_spec, optimizer_tool, request)
        result = _normalize_optimizer_result(raw_result, request, optimizer_spec.id, state.current_iteration)

        # Filter to valid IDs present in search_space and not yet tested
        tested_smiles = {r.smiles for r in state.experimental_results}
        valid_ids = [
            mid
            for mid in result.recommended_ids
            if mid in state.search_space and state.search_space[mid] not in tested_smiles
        ]
        if not valid_ids:
            state.record_tool_run(ToolRunRecord(
                tool_id=optimizer_spec.id,
                stage=ToolKind.OPTIMIZER,
                status="completed",
                inputs=request.model_dump(mode="json"),
                outputs=result.model_dump(mode="json"),
                metadata={"valid_recommendations": 0},
            ))
            return _complete_without_recommendations(state)

        result.recommended_ids = valid_ids
        state.current_bo_recommendations = valid_ids

        state.record_tool_run(ToolRunRecord(
            tool_id=optimizer_spec.id,
            stage=ToolKind.OPTIMIZER,
            status="completed",
            inputs=request.model_dump(mode="json"),
            outputs=result.model_dump(mode="json"),
            metadata={"valid_recommendations": len(valid_ids)},
        ))

        bo_round = {
            "iteration": state.current_iteration,
            "recommendations": valid_ids,
            "measurement_count": len(measurement_data),
            "tool_id": optimizer_spec.id,
            "model_metrics": result.model_metrics,
            "metadata": result.metadata,
        }
        state.bo_rounds.append(bo_round)

        state.log("bo_iteration_completed", {
            "tool_id": optimizer_spec.id,
            "recommended_ids": valid_ids,
            "recommendation_count": len(valid_ids),
            "elapsed_seconds": round(time.perf_counter() - node_started_at, 3),
        })
    except ToolError as e:
        state.record_tool_run(ToolRunRecord(
            tool_id=optimizer_spec.id,
            stage=ToolKind.OPTIMIZER,
            status="failed",
            inputs=request.model_dump(mode="json"),
            errors=[str(e)],
        ))
        emit_event(state, kind="optimizer_tool_error", node="bo_iteration", tool=optimizer_spec.id, severity="error", data={"message": str(e)})
        raise
    except NodeError:
        raise
    except Exception as e:
        state.record_tool_run(ToolRunRecord(
            tool_id=optimizer_spec.id,
            stage=ToolKind.OPTIMIZER,
            status="failed",
            inputs=request.model_dump(mode="json"),
            errors=[str(e)],
        ))
        emit_event(state, kind="optimizer_exception", node="bo_iteration", tool=optimizer_spec.id, severity="error", data={"error": str(e)})
        raise NodeError(
            f"Optimization failed: {str(e)}",
            node="bo_iteration",
            code="OPTIMIZER_EXCEPTION"
        )

    print(f"🔍 EXITING NODE: {bo_iteration_node.__name__}")
    print(f"   - New iteration: {state.current_iteration}")
    print(f"   - New status: {state.status}")
    print(f"   - Should continue: {state.should_continue()}")

    return state


def _select_optimizer_spec(state: WorkflowState, registry: ToolRegistry) -> ToolSpec:
    stage_config = state.stage_config.get("bo_iteration", {}) if isinstance(state.stage_config, dict) else {}
    explicit_tool_id = (
        stage_config.get("tool_id")
        or state.parsed_arguments.get("optimizer_tool")
        or state.parsed_arguments.get("optimizer")
    )
    if explicit_tool_id:
        spec = registry.get(str(explicit_tool_id))
        if spec.kind != ToolKind.OPTIMIZER:
            raise NodeError(
                f"Configured tool {spec.id!r} is not an optimizer",
                node="bo_iteration",
                code="OPTIMIZER_BAD_KIND",
                details={"tool_id": spec.id, "kind": spec.kind.value},
            )
        return spec

    strategy = _optimizer_strategy(state)
    if strategy:
        matches = registry.select(
            kind=ToolKind.OPTIMIZER,
            provides=["batch_recommendations"],
            accepts=[strategy],
            context=state,
        )
        if matches:
            return matches[0]

    matches = registry.select(kind=ToolKind.OPTIMIZER, provides=["batch_recommendations"], context=state)
    if matches:
        return matches[0]

    raise NodeError(
        "No optimizer tool is registered for batch recommendations",
        node="bo_iteration",
        code="OPTIMIZER_UNAVAILABLE",
        details={"strategy": strategy},
    )


def _optimizer_strategy(state: WorkflowState) -> str:
    if state.bo_config and state.bo_config.acquisition_function:
        return state.bo_config.acquisition_function
    return str(state.parsed_arguments.get("optimizer_strategy") or "expected_improvement")


def _measurement_data_from_state(state: WorkflowState) -> List[Dict[str, Any]]:
    measurement_data: List[Dict[str, Any]] = []
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
    return measurement_data


def _target_payload(target: TargetProperty) -> Dict[str, Any]:
    payload = target.model_dump(mode="json")
    mode = payload.get("mode")
    if isinstance(mode, str) and "." in mode:
        mode = mode.rsplit(".", 1)[-1]
    if isinstance(mode, str):
        payload["mode"] = {
            "MAXIMIZE": "MAX",
            "MINIMIZE": "MIN",
        }.get(mode.upper(), mode)
    return payload


def _run_optimizer_tool(spec: ToolSpec, tool: Any, request: BORecommendationRequest) -> Any:
    if hasattr(tool, "recommend"):
        return tool.recommend(request)

    if hasattr(tool, "_run"):
        return tool._run(
            targets=request.targets,
            batch_size=request.batch_size,
            encoding=request.encoding,
            measurement_data=request.measurements,
            search_space=request.search_space,
            search_space_id_column=request.search_space_id_column,
        )

    raise ToolError(
        f"Optimizer tool {spec.id} does not expose a supported execution method",
        node="bo_iteration",
        tool=spec.id,
        code="OPTIMIZER_BAD_INTERFACE",
    )


def _normalize_optimizer_result(
    raw_result: Any,
    request: BORecommendationRequest,
    tool_id: str,
    iteration: int,
) -> BORecommendationResult:
    if isinstance(raw_result, BORecommendationResult):
        raw_result.iteration = iteration
        return raw_result

    if isinstance(raw_result, str):
        raise ToolError(raw_result, node="bo_iteration", tool=tool_id, code="OPTIMIZER_FAILED")

    if isinstance(raw_result, list):
        return BORecommendationResult(
            recommended_ids=[str(molecule_id) for molecule_id in raw_result],
            iteration=iteration,
            model_metrics={
                "measurement_count": len(request.measurements or []),
                "search_space_size": len(request.search_space),
            },
            metadata={"tool_id": tool_id, "optimizer_strategy": request.optimizer_strategy},
        )

    if isinstance(raw_result, dict) and isinstance(raw_result.get("recommended_ids"), list):
        return BORecommendationResult(
            recommended_ids=[str(molecule_id) for molecule_id in raw_result["recommended_ids"]],
            acquisition_scores=dict(raw_result.get("acquisition_scores") or {}),
            model_metrics=dict(raw_result.get("model_metrics") or {}),
            iteration=iteration,
            metadata=dict(raw_result.get("metadata") or {"tool_id": tool_id}),
        )

    raise ToolError(
        f"Unexpected result type from optimizer tool: {type(raw_result)}",
        node="bo_iteration",
        tool=tool_id,
        code="OPTIMIZER_BAD_RESULT_TYPE",
    )


def _complete_without_recommendations(state: WorkflowState) -> WorkflowState:
    emit_event(state, kind="no_optimizer_recommendations", node="bo_iteration", severity="warning")
    state.status = "completed"
    state.exit_reason = "NO_RECOMMENDATIONS"
    state.current_bo_recommendations = []
    state.log("bo_iteration_no_recommendations", {
        "message": "Optimizer returned no valid recommendations. Ending campaign gracefully.",
        "iteration": state.current_iteration,
        "total_tested": len(state.experimental_results)
    })
    print("⚠️  No valid optimizer recommendations available. Ending campaign gracefully.")
    print(f"   Total molecules tested: {len(state.experimental_results)}")
    return state
