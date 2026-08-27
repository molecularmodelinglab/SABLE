"""
Enumerate molecules from starting molecules.
"""

from typing import Dict, Any, List
import time
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas.state import WorkflowState
from schemas.errors import NodeError, ToolError
from schemas.tool_registry import ToolKind, ToolRunRecord, ToolSpec
from schemas.tool_schemas import EnumerationRequest, EnumerationResult, EnumerationStrategy
from utils.telemetry import emit_event
from tools.registry import ToolRegistry, get_tool_registry

def enumerate_molecules_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Enumerate molecules from starting molecules using a registered enumerator.
    """

    print(f"🔍 ENTERING NODE: {enumerate_molecules_node.__name__}")
    print(f"   - Current iteration: {state.current_iteration}")
    print(f"   - Max iterations: {state.max_iterations}")
    print(f"   - Status: {state.status}")
    print(f"   - Should continue: {state.should_continue()}")

    node_started_at = time.perf_counter()

    state.log("enumerate_molecules_started", {
        "starting_molecules": state.starting_molecules
    })
    
    if not state.starting_molecules:
        emit_event(state, kind="no_starting_molecules", node="enumerate_molecules", severity="error")
        raise NodeError(
            "No starting molecules available for enumeration",
            node="enumerate_molecules",
            code="EMPTY_STARTING_SET",
            details={"hint": "Provide starting_molecules or adjust molecule_source"},
        )
    
    registry = get_tool_registry()
    enumerator_spec = _select_enumerator_spec(state, registry)
    state.record_tool_selection(
        registry.selection_for(
            stage=ToolKind.ENUMERATOR,
            spec=enumerator_spec,
            reason="Selected by enumeration strategy, molecule source, and available enumerator capabilities.",
        )
    )

    # Determine enumeration parameters
    max_molecules = state.parsed_arguments.get('enumeration_size', 100)
    strategy = _enum_value(state.parsed_arguments.get('enumeration_strategy')) or EnumerationStrategy.REACTION_BASED.value
    molecule_source = _enum_value(state.molecule_source)
    tool_options = _tool_options_for_enumerator(enumerator_spec, state)
    enumerator_tool = _create_enumerator_tool(registry, enumerator_spec, tool_options)

    all_molecules = {}
    molecule_counter = 0
    
    for starting_smiles in state.starting_molecules:
        batch_started_at = time.perf_counter()
        batch_limit = min(max_molecules // len(state.starting_molecules), 100)
        request = EnumerationRequest(
            starting_smiles=starting_smiles,
            strategy=strategy,
            molecule_source=molecule_source,
            healer_mode=tool_options.get("healer_mode") if _supports_tool_option(enumerator_spec, "healer_mode") else None,
            max_molecules=max(1, batch_limit),
            tool_options=dict(tool_options),
        )
        try:
            # Call the enumerator tool
            print(
                f"Enumerating: {request.max_molecules} using tool {enumerator_spec.id} "
                f"(strategy={strategy})"
            )
            raw_result = _run_enumerator_tool(enumerator_spec, enumerator_tool, request)

            # Validate tool output
            if isinstance(raw_result, str):
                emit_event(state, kind="enumerator_tool_error", node="enumerate_molecules", tool=enumerator_spec.id, severity="error", data={"message": raw_result})
                raise ToolError(raw_result, node="enumerate_molecules", tool=enumerator_spec.id, code="ENUMERATOR_FAILED")

            result = _normalize_enumeration_result(raw_result, request, enumerator_spec.id)
            for _, smiles in result.molecules.items():
                # Create unique IDs for our state
                unique_id = f"enum_{molecule_counter:04d}"
                all_molecules[unique_id] = smiles
                molecule_counter += 1

            state.record_tool_run(ToolRunRecord(
                tool_id=enumerator_spec.id,
                stage=ToolKind.ENUMERATOR,
                status="completed",
                inputs=request.model_dump(mode="json"),
                outputs=result.model_dump(mode="json"),
            ))
            
            state.log("enumerate_molecules_batch", {
                "starting_molecule": starting_smiles,
                "tool_id": enumerator_spec.id,
                "generated_count": result.count,
                "elapsed_seconds": round(time.perf_counter() - batch_started_at, 3),
            })
            
        except ToolError as e:
            state.record_tool_run(ToolRunRecord(
                tool_id=enumerator_spec.id,
                stage=ToolKind.ENUMERATOR,
                status="failed",
                inputs=request.model_dump(mode="json"),
                errors=[str(e)],
            ))
            emit_event(state, kind="enumerate_batch_failed", node="enumerate_molecules", severity="error", data={"starting": starting_smiles, "error": str(e)})
            raise
        except Exception as e:
            state.record_tool_run(ToolRunRecord(
                tool_id=enumerator_spec.id,
                stage=ToolKind.ENUMERATOR,
                status="failed",
                inputs=request.model_dump(mode="json"),
                errors=[str(e)],
            ))
            state.log("enumerate_molecules_error", {
                "starting_molecule": starting_smiles,
                "error": str(e)
            })
            emit_event(state, kind="unexpected_exception", node="enumerate_molecules", severity="error", data={"starting": starting_smiles, "error": str(e)})
            raise NodeError(str(e), node="enumerate_molecules", code="ENUMERATE_EXCEPTION")
    
    # Validate and update state with enumerated molecules
    if not all_molecules:
        emit_event(state, kind="empty_search_space", node="enumerate_molecules", severity="error")
        raise NodeError(
            "Enumeration produced an empty search space",
            node="enumerate_molecules",
            code="EMPTY_SEARCH_SPACE",
            details={"starting_smiles_count": len(state.starting_molecules)},
        )

    state.search_space = all_molecules
    
    # Assess feasibility vs. planned iterations and batch size
    batch_size = state.bo_config.batch_size if state.bo_config else 5
    tested_smiles = {r.smiles for r in state.experimental_results}
    remaining_count = len(set(state.search_space.values()) - tested_smiles)
    max_additional_rounds = remaining_count // max(1, batch_size)
    if state.max_iterations > max_additional_rounds:
        old = state.max_iterations
        state.max_iterations = max_additional_rounds
        emit_event(
            state,
            kind="max_iterations_adjusted",
            node="enumerate_molecules",
            severity="warning",
            data={
                "old_max_iterations": old,
                "new_max_iterations": state.max_iterations,
                "remaining_molecules": remaining_count,
                "batch_size": batch_size,
            },
        )
        if state.max_iterations == 0:
            emit_event(
                state,
                kind="insufficient_for_first_batch",
                node="enumerate_molecules",
                severity="error",
                data={"remaining_molecules": remaining_count, "batch_size": batch_size},
            )
    
    state.log("enumerate_molecules_completed", {
        "tool_id": enumerator_spec.id,
        "total_molecules": len(all_molecules),
        "molecule_ids": list(all_molecules.keys())[:10],
        "elapsed_seconds": round(time.perf_counter() - node_started_at, 3),
    })
    
    return state


def _select_enumerator_spec(state: WorkflowState, registry: ToolRegistry) -> ToolSpec:
    stage_config = state.stage_config.get("enumerate_molecules", {}) if isinstance(state.stage_config, dict) else {}
    explicit_tool_id = (
        stage_config.get("tool_id")
        or state.parsed_arguments.get("enumerator_tool")
        or state.parsed_arguments.get("enumerator")
    )
    if explicit_tool_id:
        spec = registry.get(str(explicit_tool_id))
        if spec.kind != ToolKind.ENUMERATOR:
            raise NodeError(
                f"Configured tool {spec.id!r} is not an enumerator",
                node="enumerate_molecules",
                code="ENUMERATOR_BAD_KIND",
                details={"tool_id": spec.id, "kind": spec.kind.value},
            )
        return spec

    strategy = _enum_value(state.parsed_arguments.get("enumeration_strategy")) or EnumerationStrategy.REACTION_BASED.value
    molecule_source = _enum_value(state.molecule_source)

    accepts: List[str] = [strategy]
    if molecule_source:
        accepts.append(molecule_source)

    matches = registry.select(
        kind=ToolKind.ENUMERATOR,
        provides=["search_space"],
        accepts=accepts,
        context=state,
    )
    if matches:
        return matches[0]

    matches = registry.select(
        kind=ToolKind.ENUMERATOR,
        provides=["search_space"],
        accepts=[strategy],
        context=state,
    )
    if matches:
        return matches[0]

    # HEALER-specific compatibility fallback. Do not make this a generic
    # selector requirement because other enumerators may not have healer_mode.
    healer_mode = _enum_value(state.parsed_arguments.get("healer_mode"))
    if healer_mode:
        matches = [
            spec
            for spec in registry.select(
                kind=ToolKind.ENUMERATOR,
                provides=["search_space"],
                accepts=[healer_mode],
                context=state,
            )
            if _supports_tool_option(spec, "healer_mode")
        ]
        if matches:
            return matches[0]

    matches = registry.select(kind=ToolKind.ENUMERATOR, provides=["search_space"], context=state)
    if matches:
        return matches[0]

    raise NodeError(
        "No enumerator tool is registered for the requested enumeration strategy",
        node="enumerate_molecules",
        code="ENUMERATOR_UNAVAILABLE",
        details={"strategy": strategy, "molecule_source": molecule_source},
    )


def _tool_options_for_enumerator(spec: ToolSpec, state: WorkflowState) -> Dict[str, Any]:
    options: Dict[str, Any] = {}
    stage_config = state.stage_config.get("enumerate_molecules", {}) if isinstance(state.stage_config, dict) else {}
    configured_options = stage_config.get("tool_options", {}) if isinstance(stage_config, dict) else {}
    if isinstance(configured_options, dict):
        options.update(configured_options)

    if _supports_tool_option(spec, "healer_mode"):
        options["healer_mode"] = (
            state.parsed_arguments.get("healer_mode")
            or options.get("healer_mode")
            or spec.config.get("healer_mode")
            or "MoleculeHEALER"
        )
        if state.run_paths.get("checkpoints"):
            options["output_dir"] = state.run_paths["checkpoints"]

    return options


def _create_enumerator_tool(registry: ToolRegistry, spec: ToolSpec, tool_options: Dict[str, Any]) -> Any:
    if _supports_tool_option(spec, "healer_mode"):
        return registry.create(
            spec.id,
            healer_mode=tool_options.get("healer_mode", "MoleculeHEALER"),
            output_dir=tool_options.get("output_dir"),
        )
    return registry.create(spec.id)


def _run_enumerator_tool(spec: ToolSpec, tool: Any, request: EnumerationRequest) -> Any:
    if hasattr(tool, "enumerate"):
        return tool.enumerate(request)

    if spec.id == "healer":
        return tool._run(
            molecule=request.starting_smiles,
            n_compositions=request.max_molecules,
        )

    if hasattr(tool, "_run"):
        try:
            return tool._run(request=request)
        except TypeError:
            pass
        try:
            return tool._run(
                starting_smiles=request.starting_smiles,
                max_molecules=request.max_molecules,
                strategy=request.strategy,
                molecule_source=request.molecule_source,
                tool_options=request.tool_options,
            )
        except TypeError:
            pass
        return tool._run(
            molecule=request.starting_smiles,
            max_molecules=request.max_molecules,
        )

    raise ToolError(
        f"Enumerator tool {spec.id} does not expose a supported execution method",
        node="enumerate_molecules",
        tool=spec.id,
        code="ENUMERATOR_BAD_INTERFACE",
    )


def _normalize_enumeration_result(raw_result: Any, request: EnumerationRequest, tool_id: str) -> EnumerationResult:
    if isinstance(raw_result, EnumerationResult):
        return raw_result

    molecules: Dict[str, str] = {}
    metadata: Dict[str, Any] = {"tool_id": tool_id, "starting_smiles": request.starting_smiles}

    if isinstance(raw_result, dict) and isinstance(raw_result.get("molecules"), dict):
        molecules = {str(mol_id): str(smiles) for mol_id, smiles in raw_result["molecules"].items()}
        raw_metadata = raw_result.get("metadata")
        if isinstance(raw_metadata, dict):
            metadata.update(raw_metadata)
    elif isinstance(raw_result, dict):
        molecules = {str(mol_id): str(smiles) for mol_id, smiles in raw_result.items()}
    else:
        raise ToolError(
            f"Unexpected enumeration result type: {type(raw_result)}",
            node="enumerate_molecules",
            tool=tool_id,
            code="ENUMERATOR_BAD_RESULT_TYPE",
        )

    return EnumerationResult(
        molecules=molecules,
        count=len(molecules),
        strategy_used=_enum_value(request.strategy) or str(request.strategy),
        metadata=metadata,
    )


def _supports_tool_option(spec: ToolSpec, option_name: str) -> bool:
    options = spec.metadata.get("tool_specific_options", [])
    return isinstance(options, list) and option_name in options


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    value_str = str(value)
    if "." in value_str:
        value_str = value_str.rsplit(".", 1)[-1]
        if value_str.isupper():
            value_str = value_str.lower()
    return value_str
