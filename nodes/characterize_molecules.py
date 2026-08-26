"""
Characterize molecules using the selected tool(s).
"""

from typing import Dict, Any, List
import asyncio
import hashlib
import time
import sys
import os
import json
from pathlib import Path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas.state import WorkflowState, ExperimentResult, ProteinTarget
from schemas.errors import NodeError
from schemas.tool_registry import ToolKind, ToolRunRecord, ToolSpec
from schemas.tool_schemas import CharacterizationRequest, CharacterizationResult
from utils.telemetry import emit_event
from schemas.characterization import (
    PROPERTY_MAPPINGS,
    normalize_property_name,
    select_characterization_tool_ids,
)
from tools.boltz import (
    BoltzExecutionKind,
    BoltzJobStatus,
    BoltzMolecule,
    BoltzPlatformAdapter,
    BoltzPlatformError,
    BoltzProvider,
    BoltzRequest,
    canonical_properties,
    normalize_self_hosted_results,
    protein_scope_id,
)
from tools.registry import get_tool_registry


def _protein_target_to_polymer(protein: ProteinTarget) -> Dict[str, Any]:
    """Convert a ProteinTarget into the Boltz polymer payload."""

    payload: Dict[str, Any] = {'chain_id': protein.chain_id}
    if protein.sequence:
        payload['sequence'] = protein.sequence
    if protein.uniprot_id:
        payload['uniprot_id'] = protein.uniprot_id
    if protein.msa:
        payload['msa'] = protein.msa
    if protein.cyclic is not None:
        payload['cyclic'] = protein.cyclic
    if protein.modifications:
        payload['modifications'] = protein.modifications
    return payload


def characterize_molecules_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Characterize molecules using the selected tool(s).
    This replaces the llm_experiment node for actual property calculation.
    """
    node_started_at = time.perf_counter()
    state.log("characterize_molecules_started", {
        "tool": state.characterization_config.get('tool', 'auto'),
        "molecules_count": len(state.current_bo_recommendations) if state.current_bo_recommendations else 0,
        "requires_boltz": state.characterization_config.get('requires_boltz', False),
        "proteins_available": len(getattr(state, 'protein_targets', []))
    })
    
    if not state.current_bo_recommendations:
        # If workflow is already completed (e.g., from BO node), just pass through
        if state.status == "completed":
            state.log("characterize_molecules_skipped", {
                "reason": "workflow_already_completed",
                "exit_reason": state.exit_reason
            })
            print(f"⏭️  Skipping characterization - workflow already completed: {state.exit_reason}")
            return state
        
        # Otherwise, end gracefully
        emit_event(state, kind="no_recommendations", node="characterize_molecules", severity="warning")
        state.status = "completed"
        state.exit_reason = "NO_MOLECULES_TO_CHARACTERIZE"
        state.log("characterize_molecules_no_batch", {
            "message": "No molecules to characterize for this iteration. Ending campaign gracefully.",
            "iteration": state.current_iteration,
            "total_tested": len(state.experimental_results)
        })
        print(f"⚠️  No molecules to characterize. Ending campaign gracefully.")
        print(f"   Total molecules tested: {len(state.experimental_results)}")
        return state
    
    registry = get_tool_registry()
    normalized_target_names = [normalize_property_name(t.name) for t in state.targets]
    selected_tool_ids = _resolve_characterization_tool_ids(state, normalized_target_names)
    tool_choice = state.characterization_config.get('tool') or _legacy_tool_label(selected_tool_ids)
    state.characterization_config['tool'] = tool_choice
    state.characterization_config['tool_ids'] = selected_tool_ids

    results: Dict[str, Dict[str, Any]] = {}
    tools_executed: List[str] = []
    boltz_metadata: Dict[str, Dict[str, Any]] = {}

    characterization_ids: List[str] = list(state.current_bo_recommendations or [])
    characterization_smiles_map: Dict[str, str] = {
        mol_id: state.search_space.get(mol_id)
        for mol_id in characterization_ids
        if state.search_space.get(mol_id)
    }
    baseline_molecule_ids: List[str] = []
    baselines_profile = state.profiling.setdefault("baselines", {})

    # Record one-time structured baseline entries for starting molecules.
    # These IDs are synthetic and stored in metadata as baselines.
    if not baselines_profile.get("starting_molecules_structured_done", False) and state.starting_molecules:
        measured_smiles = {result.smiles for result in state.experimental_results}
        known_smiles = set(characterization_smiles_map.values())

        for index, smiles in enumerate(state.starting_molecules):
            if not smiles or smiles in known_smiles or smiles in measured_smiles:
                continue

            baseline_id = f"start_baseline_{index:03d}"
            suffix = 1
            while baseline_id in characterization_smiles_map:
                baseline_id = f"start_baseline_{index:03d}_{suffix}"
                suffix += 1

            characterization_smiles_map[baseline_id] = smiles
            characterization_ids.append(baseline_id)
            baseline_molecule_ids.append(baseline_id)
            known_smiles.add(smiles)

        baselines_profile["starting_molecules_structured_done"] = True
        state.log("characterize_structured_baseline_selection", {
            "starting_molecules": len(state.starting_molecules),
            "baseline_added": len(baseline_molecule_ids),
            "already_present_or_measured": len(state.starting_molecules) - len(baseline_molecule_ids),
        })

    characterization_search_space: Dict[str, str] = dict(state.search_space)
    characterization_search_space.update(characterization_smiles_map)

    requires_boltz = state.characterization_config.get('requires_boltz', False) or (
        'binding_affinity' in normalized_target_names
    )
    boltz_only = state.characterization_config.get('boltz_only', False) or selected_tool_ids == ['boltz']
    boltz_recorded = False

    for tool_id in selected_tool_ids:
        spec = registry.get(tool_id)
        request: CharacterizationRequest | None = None
        try:
            request = _build_characterization_request(
                tool_id=tool_id,
                state=state,
                characterization_ids=characterization_ids,
                characterization_smiles_map=characterization_smiles_map,
                characterization_search_space=characterization_search_space,
                baseline_molecule_ids=baseline_molecule_ids,
                baselines_profile=baselines_profile,
            )
            if not request.molecule_ids:
                emit_event(
                    state,
                    kind="characterizer_no_molecules",
                    node="characterize_molecules",
                    tool=tool_id,
                    severity="warning",
                )
                continue

            tool_started_at = time.perf_counter()
            if tool_id == "boltz" and _boltz_provider(state) == "platform":
                result = _run_boltz_platform_characterization(state, request)
            else:
                tool = _create_characterization_tool(registry, spec, request)
                result = _run_characterization_tool(spec, tool, request)
            _merge_characterization_result(results, boltz_metadata, result)
            tools_executed.append(tool_id)

            if tool_id == "boltz":
                boltz_recorded = True

            state.record_tool_run(ToolRunRecord(
                tool_id=tool_id,
                stage=ToolKind.CHARACTERIZER,
                status="completed",
                inputs=_characterization_run_inputs(request),
                outputs={
                    "molecule_count": len(result.results),
                    "failed_molecule_count": len(result.failed_molecules),
                },
                metadata={"result_metadata": result.metadata},
            ))
            state.log(f"characterize_{tool_id}_completed", {
                "molecules_processed": len(result.results),
                "properties": _result_property_names(result),
                "elapsed_seconds": round(time.perf_counter() - tool_started_at, 3),
            })
        except NodeError as exc:
            state.record_tool_run(ToolRunRecord(
                tool_id=tool_id,
                stage=ToolKind.CHARACTERIZER,
                status="failed",
                inputs=_characterization_run_inputs(request) if request else {"molecule_count": len(characterization_ids)},
                errors=[str(exc)],
            ))
            raise
        except Exception as exc:
            state.record_tool_run(ToolRunRecord(
                tool_id=tool_id,
                stage=ToolKind.CHARACTERIZER,
                status="failed",
                inputs=_characterization_run_inputs(request) if request else {"molecule_count": len(characterization_ids)},
                errors=[str(exc)],
            ))
            emit_event(
                state,
                kind=f"{tool_id}_tool_exception",
                node="characterize_molecules",
                tool=tool_id,
                severity="error",
                data={"error": str(exc)},
            )
            if tool_id == "boltz":
                error_code = "BOLTZ_TOOL_ERROR" if type(exc).__name__ == "ToolException" else "BOLTZ_EXCEPTION"
                raise NodeError(
                    f"Boltz execution failed: {str(exc)}",
                    node="characterize_molecules",
                    code=error_code,
                )

    if (requires_boltz or boltz_only or "boltz" in selected_tool_ids) and "boltz" in selected_tool_ids and not boltz_recorded:
        raise NodeError(
            "Binding affinity requested but Boltz did not complete successfully",
            node="characterize_molecules",
            code="BOLTZ_NOT_RECORDED",
        )

    # Prepare metadata for experiment results
    executed_tool_names: List[str] = []
    for tool_id in tools_executed:
        if tool_id not in executed_tool_names:
            executed_tool_names.append(tool_id)

    # Convert results to ExperimentResult objects
    mapped_any = False
    baseline_molecule_id_set = set(baseline_molecule_ids)
    result_molecule_ids: List[str] = []
    seen_result_ids = set()
    for mol_id in [*characterization_ids, *baseline_molecule_ids]:
        if mol_id in seen_result_ids:
            continue
        seen_result_ids.add(mol_id)
        result_molecule_ids.append(mol_id)
    for mol_id in result_molecule_ids:
        smiles = characterization_smiles_map.get(mol_id) or state.search_space.get(mol_id)
        if smiles and mol_id in results:
            
            # Map properties to target names
            mapped_properties = {}
            for target in state.targets:
                target_name_lower = normalize_property_name(target.name)
                
                # Try to find the property in results
                found = False
                for result_key, result_value in results[mol_id].items():
                    result_key_lower = normalize_property_name(result_key)
                    
                    # Direct match
                    if result_key_lower == target_name_lower:
                        mapped_properties[target.name] = float(result_value)
                        found = True
                        break
                    
                    # Check mappings
                    if target_name_lower in PROPERTY_MAPPINGS:
                        rdkit_name, stoplight_name = PROPERTY_MAPPINGS[target_name_lower]
                        if (rdkit_name and normalize_property_name(rdkit_name) == result_key_lower) or \
                           (stoplight_name and normalize_property_name(stoplight_name) == result_key_lower):
                            mapped_properties[target.name] = float(result_value)
                            found = True
                            break

                    # Boltz Platform fallback: binding_affinity -> boltz_optimization_score
                    if target_name_lower == "binding_affinity" and result_key_lower == "boltz_optimization_score":
                        mapped_properties[target.name] = float(result_value)
                        found = True
                        break

                # If not found, try to get any similar property
                if not found:
                    # Look for partial matches
                    for result_key, result_value in results[mol_id].items():
                        if target_name_lower in normalize_property_name(result_key) or \
                           normalize_property_name(result_key) in target_name_lower:
                            mapped_properties[target.name] = float(result_value)
                            break
            
            # Create ExperimentResult
            if mapped_properties:
                metadata: Dict[str, Any] = {
                    "characterization_tool": str(tool_choice),
                    "selected_tool_ids": list(selected_tool_ids),
                    "tools_used": executed_tool_names,
                    "all_properties": dict(results[mol_id]),
                    "is_starting_molecule_baseline": mol_id in baseline_molecule_id_set,
                }

                boltz_info = boltz_metadata.get(mol_id)
                if boltz_info:
                    metadata["boltz"] = boltz_info

                exp_result = ExperimentResult(
                    molecule_id=mol_id,
                    smiles=smiles,
                    iteration=state.current_iteration,
                    properties=mapped_properties,
                    metadata=metadata
                )
                state.add_experimental_result(exp_result)
                mapped_any = True

    if not mapped_any:
        emit_event(state, kind="no_properties_mapped", node="characterize_molecules", severity="error", data={"count": len(state.current_bo_recommendations)})
        raise NodeError("Characterization produced no mappable properties for targets", node="characterize_molecules", code="NO_USABLE_DATA")
    
    state.log("characterize_molecules_completed", {
        "tool_used": str(tool_choice),
        "tool_ids": selected_tool_ids,
        "tools_executed": executed_tool_names,
        "molecules_characterized": len(results),
        "properties_mapped": len(state.experimental_results),
        "boltz_required": requires_boltz or boltz_only,
        "elapsed_seconds": round(time.perf_counter() - node_started_at, 3),
    })

    print(f"🔍 EXITING NODE: {characterize_molecules_node.__name__}")
    print(f"   - New iteration: {state.current_iteration}")
    print(f"   - New status: {state.status}")
    print(f"   - Should continue: {state.should_continue()}")
    
    return state


def _build_characterization_request(
    tool_id: str,
    state: WorkflowState,
    characterization_ids: List[str],
    characterization_smiles_map: Dict[str, str],
    characterization_search_space: Dict[str, str],
    baseline_molecule_ids: List[str],
    baselines_profile: Dict[str, Any],
) -> CharacterizationRequest:
    ids_to_process = list(characterization_ids)
    properties = [target.name for target in state.targets]
    proteins_payload: List[Dict[str, Any]] = []
    tool_options: Dict[str, Any] = {}

    if tool_id == "boltz":
        ids_to_process = _boltz_ids_to_process(
            state=state,
            characterization_ids=characterization_ids,
            characterization_smiles_map=characterization_smiles_map,
            characterization_search_space=characterization_search_space,
            baseline_molecule_ids=baseline_molecule_ids,
            baselines_profile=baselines_profile,
        )
        proteins = getattr(state, 'protein_targets', [])
        if not proteins:
            emit_event(
                state,
                kind="boltz_missing_proteins",
                node="characterize_molecules",
                severity="error",
                data={"message": "Binding affinity requested but no protein targets available."}
            )
            raise NodeError(
                "Binding affinity requested but no protein targets are available",
                node="characterize_molecules",
                code="BOLTZ_MISSING_PROTEINS",
            )
        proteins_payload = [_protein_target_to_polymer(protein) for protein in proteins]
        if _boltz_provider(state) != "platform":
            tool_options = _boltz_tool_options(state)

    smiles_by_id = {
        mol_id: characterization_search_space.get(mol_id) or characterization_smiles_map.get(mol_id)
        for mol_id in ids_to_process
    }
    smiles_by_id = {mol_id: smiles for mol_id, smiles in smiles_by_id.items() if smiles}

    if tool_id == "boltz" and not smiles_by_id:
        emit_event(
            state,
            kind="boltz_missing_ligands",
            node="characterize_molecules",
            severity="error",
            data={"message": "Binding affinity requested but no ligand SMILES available."}
        )
        raise NodeError(
            "Binding affinity requested but no ligand SMILES are available",
            node="characterize_molecules",
            code="BOLTZ_MISSING_LIGANDS",
        )

    return CharacterizationRequest(
        smiles=smiles_by_id,
        properties=properties,
        molecule_ids=list(smiles_by_id.keys()),
        search_space=smiles_by_id,
        proteins=proteins_payload,
        precision=2,
        tool_options=tool_options,
    )


def _boltz_ids_to_process(
    state: WorkflowState,
    characterization_ids: List[str],
    characterization_smiles_map: Dict[str, str],
    characterization_search_space: Dict[str, str],
    baseline_molecule_ids: List[str],
    baselines_profile: Dict[str, Any],
) -> List[str]:
    boltz_ids_to_process = list(characterization_ids)
    starting_affinity_baseline_done = bool(
        baselines_profile.get("binding_affinity_starting_molecules_done", False)
    )

    affinity_baseline_added = 0
    if not starting_affinity_baseline_done and state.starting_molecules:
        measured_affinity_smiles = {
            result.smiles
            for result in state.experimental_results
            if any(
                normalize_property_name(prop_name) in {"binding_affinity", "affinity"}
                for prop_name in result.properties
            )
        }
        known_smiles = set(characterization_smiles_map.values())

        for index, smiles in enumerate(state.starting_molecules):
            if not smiles or smiles in known_smiles or smiles in measured_affinity_smiles:
                continue

            baseline_id = f"start_baseline_{index:03d}"
            suffix = 1
            while baseline_id in characterization_smiles_map:
                baseline_id = f"start_baseline_{index:03d}_{suffix}"
                suffix += 1

            characterization_smiles_map[baseline_id] = smiles
            characterization_search_space[baseline_id] = smiles
            boltz_ids_to_process.append(baseline_id)
            baseline_molecule_ids.append(baseline_id)
            known_smiles.add(smiles)
            affinity_baseline_added += 1

        baselines_profile["binding_affinity_starting_molecules_done"] = True
        state.log("characterize_boltz_baseline_selection", {
            "starting_molecules": len(state.starting_molecules),
            "baseline_added": affinity_baseline_added,
            "already_had_affinity": len(state.starting_molecules) - affinity_baseline_added,
        })

    return boltz_ids_to_process


def _boltz_tool_options(state: WorkflowState) -> Dict[str, Any]:
    boltz_config = state.characterization_config.get('boltz_config', {}) or {}

    if not boltz_config.get('cif_save_dir'):
        env_cif_dir = os.environ.get("BOLTZ_CIF_DIR")
        if env_cif_dir:
            boltz_config['cif_save_dir'] = env_cif_dir
        else:
            run_paths = getattr(state, 'run_paths', {}) or {}
            artifacts_dir = run_paths.get('artifacts')
            if artifacts_dir:
                boltz_config['cif_save_dir'] = str(Path(artifacts_dir) / "boltz_cifs")

    state.characterization_config['boltz_config'] = boltz_config
    base_url = boltz_config.get('base_url') or os.environ.get("BOLTZ_BASE_URL", "").strip()
    api_token = boltz_config.get('api_token') or os.environ.get("BOLTZ_API_TOKEN", "").strip()

    if not base_url or not api_token:
        emit_event(
            state,
            kind="boltz_unconfigured",
            node="characterize_molecules",
            severity="error",
            data={"message": "Boltz credentials missing while binding affinity is required."},
        )
        raise NodeError(
            "Binding affinity requested but Boltz is not configured",
            node="characterize_molecules",
            code="BOLTZ_UNCONFIGURED",
        )

    return {
        "base_url": base_url,
        "api_token": api_token,
        "timeout": boltz_config.get('timeout', 900.0),
        "max_retries": boltz_config.get('max_retries', 1),
        "fetch_cif": boltz_config.get('fetch_cif', True),
        "cif_save_dir": boltz_config.get('cif_save_dir'),
        "latency_seconds": boltz_config.get('latency_seconds'),
        "poll_interval": boltz_config.get('poll_interval'),
        "poll_attempts": boltz_config.get('poll_attempts'),
        "constraints": boltz_config.get('constraints'),
        "templates": boltz_config.get('templates'),
    }


def _boltz_provider(state: WorkflowState) -> str:
    config = state.characterization_config.get("boltz", {}) or {}
    return str(config.get("provider", "self_hosted"))


def _run_boltz_platform_characterization(
    state: WorkflowState,
    request: CharacterizationRequest,
) -> CharacterizationResult:
    from server.database import get_db_context
    from server.services.credential_service import credential_service
    from server.services.provider_job_service import provider_job_service

    config = state.characterization_config.get("boltz", {}) or {}
    execution = state.extensions.get("execution", {}) or {}
    run_id = execution.get("run_id")
    user_id = execution.get("user_id")
    credential_id = config.get("credential_id")
    if not run_id or not user_id or not credential_id:
        raise NodeError(
            "Boltz Platform execution context is incomplete",
            node="characterize_molecules",
            code="BOLTZ_PLATFORM_CONTEXT_MISSING",
        )

    preference = config.get("execution_preference", "auto")
    if preference == "prediction":
        raise NodeError(
            "Boltz Platform prediction execution is not supported yet",
            node="characterize_molecules",
            code="BOLTZ_PLATFORM_EXECUTION_UNSUPPORTED",
        )

    scope_id = protein_scope_id(request.proteins)
    platform_request = BoltzRequest(
        provider=BoltzProvider.PLATFORM,
        execution_kind=BoltzExecutionKind.LIBRARY_SCREEN,
        molecules=[
            BoltzMolecule(id=molecule_id, smiles=smiles)
            for molecule_id, smiles in request.search_space.items()
        ],
        proteins=request.proteins,
        protein_scope_id=scope_id,
    )
    idempotency_payload = json.dumps(
        {
            "run_id": run_id,
            "molecules": [molecule.model_dump(mode="json") for molecule in platform_request.molecules],
            "protein_scope_id": scope_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    platform_request = platform_request.model_copy(update={
        "options": {
            "idempotency_key": f"sable-{hashlib.sha256(idempotency_payload).hexdigest()}"
        }
    })

    result_rows = None
    with get_db_context() as db:
        existing_job = provider_job_service.find_for_request(
            db,
            run_id=run_id,
            request=platform_request,
        )
        if (
            existing_job is not None
            and existing_job.status == BoltzJobStatus.SUCCEEDED.value
            and existing_job.completed_items + existing_job.failed_items >= existing_job.total_items
        ):
            result_rows = provider_job_service.load_results(existing_job)

        credential = credential_service.get_owned(db, credential_id, user_id)
        if result_rows is None and (credential is None or credential.status != "active"):
            raise NodeError(
                "An active owned Boltz Platform credential is required",
                node="characterize_molecules",
                code="BOLTZ_PLATFORM_CREDENTIAL_INVALID",
            )
        api_key = (
            credential_service.decrypt(credential.encrypted_secret)
            if result_rows is None
            else None
        )
        submission = (
            provider_job_service.submission_for(existing_job, platform_request)
            if existing_job is not None
            else None
        )
        provider_job_id = existing_job.id if existing_job is not None else None

    if result_rows is None:
        artifacts_dir = (state.run_paths or {}).get("artifacts")
        artifact_root = Path(artifacts_dir) / "boltz_platform" if artifacts_dir else None
        adapter = BoltzPlatformAdapter(api_key, artifact_root=artifact_root)
        result_rows = asyncio.run(_execute_boltz_platform_job(
            adapter=adapter,
            request=platform_request,
            submission=submission,
            run_id=run_id,
            user_id=user_id,
            credential_id=credential_id,
            provider_job_id=provider_job_id,
        ))

    results: Dict[str, Dict[str, Any]] = {}
    per_molecule_metadata: Dict[str, Dict[str, Any]] = {}
    failed_molecules: List[str] = []
    for row in result_rows:
        if row.status == BoltzJobStatus.SUCCEEDED:
            results[row.molecule_id] = canonical_properties(row.metrics)
        else:
            failed_molecules.append(row.molecule_id)
        per_molecule_metadata[row.molecule_id] = {
            "protein_scope_id": scope_id,
            "provider": BoltzProvider.PLATFORM.value,
            "provider_result_id": row.provider_result_id,
            "job_id": row.provider_result_id,
            "warnings": list(row.warnings),
            "reliability_weighted": False,
            "structure_path": row.structure_path,
            "cif_file": row.structure_path,
        }

    missing = set(request.molecule_ids) - set(results) - set(failed_molecules)
    failed_molecules.extend(sorted(missing))
    return CharacterizationResult(
        results=results,
        failed_molecules=failed_molecules,
        metadata={
            "provider": BoltzProvider.PLATFORM.value,
            "protein_scope_id": scope_id,
            "per_molecule_metadata": per_molecule_metadata,
            "per_ligand": per_molecule_metadata,
        },
    )


async def _execute_boltz_platform_job(
    *,
    adapter: BoltzPlatformAdapter,
    request: BoltzRequest,
    submission: Any,
    run_id: str,
    user_id: str,
    credential_id: str,
    provider_job_id: Any,
) -> List[Any]:
    from server.database import get_db_context
    from server.models.provider_job import ProviderJob
    from server.services.provider_job_service import provider_job_service

    if submission is None:
        submission = await adapter.submit(request)
        with get_db_context() as db:
            job = provider_job_service.create(
                db,
                run_id=run_id,
                user_id=user_id,
                credential_id=credential_id,
                request=request,
                submission=submission,
            )
            provider_job_id = job.id
            db.commit()

    poll_interval = max(float(os.getenv("BOLTZ_PLATFORM_POLL_INTERVAL", "5")), 0.0)
    while submission.status not in {
        BoltzJobStatus.SUCCEEDED,
        BoltzJobStatus.FAILED,
        BoltzJobStatus.STOPPED,
    }:
        status = await adapter.poll(submission)
        submission = submission.model_copy(update={"status": status})
        persisted_status = (
            BoltzJobStatus.RUNNING.value
            if status == BoltzJobStatus.SUCCEEDED
            else status.value
        )
        with get_db_context() as db:
            job = db.query(ProviderJob).filter(ProviderJob.id == provider_job_id).one()
            provider_job_service.update_status(db, job, persisted_status)
            db.commit()

        if status == BoltzJobStatus.RUNNING:
            try:
                partial_results = await adapter.collect_results(submission)
            except BoltzPlatformError:
                partial_results = []
            if partial_results:
                with get_db_context() as db:
                    job = db.query(ProviderJob).filter(ProviderJob.id == provider_job_id).one()
                    provider_job_service.store_results(db, job, partial_results)
                    db.commit()

        if status not in {
            BoltzJobStatus.SUCCEEDED,
            BoltzJobStatus.FAILED,
            BoltzJobStatus.STOPPED,
        }:
            await asyncio.sleep(poll_interval)

    if submission.status != BoltzJobStatus.SUCCEEDED:
        raise NodeError(
            f"Boltz Platform job ended with status {submission.status.value}",
            node="characterize_molecules",
            code="BOLTZ_PLATFORM_JOB_FAILED",
        )

    results = await adapter.collect_results(submission)
    with get_db_context() as db:
        job = db.query(ProviderJob).filter(ProviderJob.id == provider_job_id).one()
        provider_job_service.store_results(db, job, results)
        provider_job_service.update_status(db, job, BoltzJobStatus.SUCCEEDED.value)
        db.commit()
    return results


def _create_characterization_tool(registry: Any, spec: ToolSpec, request: CharacterizationRequest) -> Any:
    if spec.id == "boltz":
        tool_config = {
            key: value
            for key, value in request.tool_options.items()
            if key not in {"constraints", "templates"} and value is not None
        }
        return registry.create(spec.id, **tool_config)
    return registry.create(spec.id)


def _run_characterization_tool(
    spec: ToolSpec,
    tool: Any,
    request: CharacterizationRequest,
) -> CharacterizationResult:
    if hasattr(tool, "characterize"):
        return _normalize_characterization_result(tool.characterize(request), spec.id)

    if spec.id == "rdkit":
        raw_result = tool._run(
            search_space=request.search_space,
            ids_to_process=request.molecule_ids,
        )
        return _normalize_characterization_result(raw_result, spec.id)

    if spec.id == "stoplight":
        raw_result = tool._run(
            precision=request.precision,
            search_space=request.search_space,
            ids_to_process=request.molecule_ids,
        )
        return _normalize_characterization_result(raw_result, spec.id)

    if spec.id == "boltz":
        return _run_boltz_characterization_tool(tool, request)

    if hasattr(tool, "_run"):
        raw_result = tool._run(request=request)
        return _normalize_characterization_result(raw_result, spec.id)

    raise TypeError(f"Characterizer tool {spec.id} does not expose a supported execution method")


def _run_boltz_characterization_tool(tool: Any, request: CharacterizationRequest) -> CharacterizationResult:
    print(
        "Invoking Boltz affinity tool for",
        len(request.search_space),
        "ligands and",
        len(request.proteins),
        "proteins",
    )
    response = asyncio.run(tool._arun(
        ligands=request.search_space,
        polymers=request.proteins,
        constraints=request.tool_options.get('constraints'),
        templates=request.tool_options.get('templates'),
    ))

    print("Boltz tool returned response")

    if isinstance(response, str):
        try:
            response_data = json.loads(response)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to decode Boltz response: {exc}")
    else:
        response_data = response

    per_ligand = response_data.get('per_ligand', {}) if isinstance(response_data, dict) else {}
    scope_id = protein_scope_id(request.proteins)
    normalized_results = normalize_self_hosted_results(per_ligand, scope_id)
    results: Dict[str, Dict[str, Any]] = {}
    per_molecule_metadata: Dict[str, Dict[str, Any]] = {}

    for normalized in normalized_results:
        if normalized.status != BoltzJobStatus.SUCCEEDED:
            continue
        results[normalized.molecule_id] = canonical_properties(normalized.metrics)
        raw_info = per_ligand.get(normalized.molecule_id, {})
        per_molecule_metadata[normalized.molecule_id] = {
            **(raw_info if isinstance(raw_info, dict) else {}),
            'protein_scope_id': scope_id,
            'reliability_weighted': False,
        }

    missing_affinity = set(request.search_space.keys()) - set(results.keys())
    if missing_affinity:
        raise NodeError(
            "Boltz did not return affinity for all requested ligands",
            node="characterize_molecules",
            code="BOLTZ_MISSING_AFFINITY",
            details={"missing_ligand_ids": sorted(missing_affinity)},
        )

    return CharacterizationResult(
        results=results,
        failed_molecules=[],
        metadata={
            "per_ligand": per_ligand,
            "per_molecule_metadata": per_molecule_metadata,
            "protein_scope_id": scope_id,
        },
    )


def _normalize_characterization_result(raw_result: Any, tool_id: str) -> CharacterizationResult:
    if isinstance(raw_result, CharacterizationResult):
        raw_result.metadata.setdefault("tool_id", tool_id)
        return raw_result

    if isinstance(raw_result, str):
        raise ValueError(raw_result)

    if isinstance(raw_result, dict):
        return CharacterizationResult(
            results={
                str(mol_id): dict(props)
                for mol_id, props in raw_result.items()
                if isinstance(props, dict)
            },
            metadata={"tool_id": tool_id},
        )

    raise TypeError(f"Unexpected characterization result type from {tool_id}: {type(raw_result)}")


def _merge_characterization_result(
    results: Dict[str, Dict[str, Any]],
    boltz_metadata: Dict[str, Dict[str, Any]],
    result: CharacterizationResult,
) -> None:
    for mol_id, props in result.results.items():
        results.setdefault(mol_id, {})
        results[mol_id].update(props)

    per_molecule_metadata = result.metadata.get("per_molecule_metadata", {})
    if isinstance(per_molecule_metadata, dict):
        for mol_id, metadata in per_molecule_metadata.items():
            if isinstance(metadata, dict):
                boltz_metadata[mol_id] = metadata


def _characterization_run_inputs(request: CharacterizationRequest) -> Dict[str, Any]:
    return {
        "molecule_count": len(request.molecule_ids),
        "properties": list(request.properties),
        "protein_count": len(request.proteins),
    }


def _result_property_names(result: CharacterizationResult) -> List[str]:
    names: List[str] = []
    for props in result.results.values():
        for name in props:
            if name not in names:
                names.append(name)
    return names


def _resolve_characterization_tool_ids(state: WorkflowState, normalized_target_names: List[str]) -> List[str]:
    configured_tool_ids = state.characterization_config.get('tool_ids')
    if isinstance(configured_tool_ids, list) and configured_tool_ids:
        return [str(tool_id) for tool_id in configured_tool_ids]

    legacy_tool = state.characterization_config.get('tool')
    legacy_ids = _tool_ids_from_legacy_label(legacy_tool)
    if legacy_ids:
        return legacy_ids

    return select_characterization_tool_ids(normalized_target_names)


def _tool_ids_from_legacy_label(value: Any) -> List[str]:
    label = value.value if hasattr(value, "value") else value
    label = str(label or "").strip().lower()
    if label in {"rdkit", "stoplight", "boltz"}:
        return [label]
    if label == "combined":
        return ["rdkit", "stoplight"]
    return []


def _legacy_tool_label(tool_ids: List[str]) -> str:
    if tool_ids == ["rdkit"]:
        return "rdkit"
    if tool_ids == ["stoplight"]:
        return "stoplight"
    if tool_ids == ["boltz"]:
        return "boltz"
    if tool_ids:
        return "combined"
    return "auto"
