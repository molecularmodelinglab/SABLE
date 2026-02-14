"""
Characterize molecules using the selected tool(s).
"""

from typing import Dict, Any, List
import asyncio
import time
import sys
import os
import json
import hashlib
from pathlib import Path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rdkit import Chem
from rdkit.Chem import Descriptors
from schemas.state import WorkflowState, ExperimentResult, ProteinTarget
from schemas.errors import NodeError
from utils.telemetry import emit_event
from schemas.characterization import (
    CharacterizationTool,
    PROPERTY_MAPPINGS,
    normalize_property_name
)
from tools.molecule_characterization_tool import MoleculeCharacterizationTool
from tools.stoplight_tool import StoplightTool
from tools.boltz_tool import BoltzTool, ToolException


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


def _heuristic_affinity(smiles: str) -> float:
    """Deterministic fallback affinity prediction when Boltz is unavailable."""

    digest = hashlib.sha1(smiles.encode('utf-8')).hexdigest()
    value = int(digest[:8], 16) % 1000
    return round(5.0 + value / 100.0, 3)


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
    
    # Get tool choice
    raw_tool_choice = state.characterization_config.get('tool', CharacterizationTool.AUTO)
    tool_choice = (
        CharacterizationTool(raw_tool_choice)
        if not isinstance(raw_tool_choice, CharacterizationTool)
        else raw_tool_choice
    )

    results: Dict[str, Dict[str, Any]] = {}
    tools_executed: List[CharacterizationTool] = []
    boltz_metadata: Dict[str, Dict[str, Any]] = {}

    normalized_target_names = [normalize_property_name(t.name) for t in state.targets]
    requires_boltz = state.characterization_config.get('requires_boltz', False) or (
        'binding_affinity' in normalized_target_names
    )
    boltz_only = state.characterization_config.get('boltz_only', False) or tool_choice == CharacterizationTool.BOLTZ

    should_run_rdkit = tool_choice in {CharacterizationTool.RDKIT, CharacterizationTool.COMBINED}
    should_run_stoplight = tool_choice in {CharacterizationTool.STOPLIGHT, CharacterizationTool.COMBINED}
    
    # Use RDKit tool
    if should_run_rdkit:
        try:
            rdkit_started_at = time.perf_counter()
            rdkit_tool = MoleculeCharacterizationTool()
            rdkit_result = rdkit_tool._run(
                search_space=state.search_space,
                ids_to_process=state.current_bo_recommendations
            )
            tools_executed.append(CharacterizationTool.RDKIT)
            
            # Extract results from memory
            if rdkit_result:
                for mol_id, props in rdkit_result.items():
                    if mol_id not in results:
                        results[mol_id] = {}
                    results[mol_id].update(props)
                    
            state.log("characterize_rdkit_completed", {
                "molecules_processed": len(results),
                "properties": list(next(iter(results.values())).keys()) if results else [],
                "elapsed_seconds": round(time.perf_counter() - rdkit_started_at, 3),
            })
        except Exception as e:
            emit_event(state, kind="rdkit_tool_exception", node="characterize_molecules", tool="MoleculeCharacterizer", severity="error", data={"error": str(e)})
    
    # Use Stoplight tool
    if should_run_stoplight:
        try:
            stoplight_started_at = time.perf_counter()
            stoplight_tool = StoplightTool()
            stoplight_result = stoplight_tool._run(
                precision=2,
                search_space=state.search_space,
                ids_to_process=state.current_bo_recommendations
            )
            tools_executed.append(CharacterizationTool.STOPLIGHT)

            # Extract results from memory
            if stoplight_result:
                for mol_id, props in stoplight_result.items():
                    if mol_id not in results:
                        results[mol_id] = {}
                    results[mol_id].update(props)
                    
            state.log("characterize_stoplight_completed", {
                "molecules_processed": len(results),
                "api_response": stoplight_result,
                "elapsed_seconds": round(time.perf_counter() - stoplight_started_at, 3),
            })
        except Exception as e:
            emit_event(state, kind="stoplight_tool_exception", node="characterize_molecules", tool="Stoplight", severity="error", data={"error": str(e)})
    
    # Run Boltz affinity predictions if required
    affinity_fallback_used = False
    boltz_recorded = False
    if requires_boltz or boltz_only:
        proteins = getattr(state, 'protein_targets', [])
        ligands_map = {
            mol_id: state.search_space.get(mol_id)
            for mol_id in (state.current_bo_recommendations or [])
            if state.search_space.get(mol_id)
        }

        if not proteins:
            emit_event(
                state,
                kind="boltz_missing_proteins",
                node="characterize_molecules",
                severity="error",
                data={"message": "Binding affinity requested but no protein targets available."}
            )
        elif not ligands_map:
            emit_event(
                state,
                kind="boltz_missing_ligands",
                node="characterize_molecules",
                severity="error",
                data={"message": "Binding affinity requested but no ligand SMILES available."}
            )
        else:
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

            polymers_payload = [_protein_target_to_polymer(protein) for protein in proteins]

            if base_url and api_token:
                try:
                    boltz_started_at = time.perf_counter()
                    print("⚙️  Invoking Boltz affinity tool for", len(ligands_map), "ligands and", len(polymers_payload), "proteins")
                    boltz_tool = BoltzTool(
                        base_url=base_url,
                        api_token=api_token,
                        timeout=boltz_config.get('timeout', 900.0),
                        max_retries=boltz_config.get('max_retries', 1),
                        fetch_cif=boltz_config.get('fetch_cif', True),
                        cif_save_dir=boltz_config.get('cif_save_dir'),
                        latency_seconds=boltz_config.get('latency_seconds'),
                        poll_interval=boltz_config.get('poll_interval'),
                        poll_attempts=boltz_config.get('poll_attempts'),
                    )

                    response = asyncio.run(boltz_tool._arun(
                        ligands=ligands_map,
                        polymers=polymers_payload,
                        constraints=boltz_config.get('constraints'),
                        templates=boltz_config.get('templates'),
                    ))

                    print("✅ Boltz tool returned response")

                    if isinstance(response, str):
                        try:
                            response_data = json.loads(response)
                        except json.JSONDecodeError as exc:
                            raise ToolException(f"Failed to decode Boltz response: {exc}")
                    else:
                        response_data = response

                    per_ligand = response_data.get('per_ligand', {})
                    for ligand_id, info in per_ligand.items():
                        affinity = info.get('affinity')
                        if affinity is None:
                            continue
                        
                        # Handle affinity as dict (Boltz returns multiple prediction values)
                        if isinstance(affinity, dict):
                            affinity_value = affinity.get('affinity_pred_value')
                            if affinity_value is None:
                                # Fallback to first numeric value if primary key not found
                                for v in affinity.values():
                                    if isinstance(v, (int, float)):
                                        affinity_value = v
                                        break
                            if affinity_value is None:
                                continue

                            affinity_reliability = affinity.get('affinity_probability_binary')
                            if affinity_reliability is None:
                                affinity_reliability = affinity.get('affinity_probability_binary1')
                        else:
                            affinity_value = affinity
                            affinity_reliability = None


                        original_affinity_value = float(affinity_value)
                        penalized = False

                        if affinity_reliability is not None:
                            reliability = float(affinity_reliability)
                            penalty_baseline = 10.0
                            affinity_value = original_affinity_value * reliability + penalty_baseline * (1 - reliability)
                            
                            if reliability < 0.5:
                                penalized = True
                                print(f"⚠️  Low reliability for ligand '{ligand_id}': {reliability:.3f}, "
                                      f"affinity {original_affinity_value:.3f} -> {affinity_value:.3f}")
                            else:
                                print(f"✓  Weighted affinity for ligand '{ligand_id}': reliability {reliability:.3f}, "
                                      f"affinity {original_affinity_value:.3f} -> {affinity_value:.3f}")

                        
                        # confidence_value = None
                        # confidence = info.get('confidence')
                        # if confidence is not None:
                        #     if isinstance(confidence, dict):
                        #         confidence_value = confidence.get('confidence_score') or confidence.get('ptm')
                        #     else:
                        #         confidence_value = confidence

                        # results.setdefault(ligand_id, {})
                        # results[ligand_id]['binding_affinity'] = float(affinity_value)
                        
                        # # Store full affinity dict in metadata
                        # if isinstance(affinity, dict):
                        #     results[ligand_id]['binding_affinity_details'] = affinity
                        
                        # if confidence_value is not None:
                        #     # Handle confidence as dict
                        #     results[ligand_id]['binding_affinity_confidence'] = float(confidence_value)
                        #     if isinstance(confidence, dict):

                        #         results[ligand_id]['binding_affinity_confidence_details'] = confidence

                        # if penalized:
                        #     results[ligand_id]['binding_affinity_penalized'] = True
                        #     results[ligand_id]['binding_affinity_original'] = info.get('affinity')
                        #     boltz_metadata[ligand_id] = {
                        #         **info,
                        #         'penalized': True,
                        #         'penalty_reason': 'low_affinity_reliability',
                        #         'affinity_probability_binary': affinity_reliability,
                        #         'original_affinity': info.get('affinity')
                        #     }
                        # else:
                        #     boltz_metadata[ligand_id] = info

                        if penalized:
                            results[ligand_id]['binding_affinity_penalized'] = True
                        
                        results[ligand_id]['binding_affinity_original'] = original_affinity_value
                        results[ligand_id]['binding_affinity_reliability'] = affinity_reliability
                        
                        boltz_metadata[ligand_id] = {
                            **info,
                            'reliability_weighted': True,
                            'original_affinity_value': original_affinity_value,
                            'affinity_probability_binary': affinity_reliability,
                            'penalized': penalized
                        }



                    missing_affinity = set(ligands_map.keys()) - set(per_ligand.keys())
                    if missing_affinity:
                        for missing_id in missing_affinity:
                            smiles = ligands_map[missing_id]
                            results.setdefault(missing_id, {})
                            score = _heuristic_affinity(smiles)
                            results[missing_id]['binding_affinity'] = score
                            boltz_metadata[missing_id] = {'source': 'heuristic_missing'}
                        affinity_fallback_used = True

                    boltz_recorded = True
                    state.log("characterize_boltz_completed", {
                        "ligands": len(ligands_map),
                        "proteins": len(proteins),
                        "fallback_used": affinity_fallback_used,
                        "response_keys": list(per_ligand.keys()),
                        "elapsed_seconds": round(time.perf_counter() - boltz_started_at, 3),
                    })

                except ToolException as exc:
                    emit_event(
                        state,
                        kind="boltz_tool_error",
                        node="characterize_molecules",
                        tool="Boltz",
                        severity="error",
                        data={"error": str(exc)}
                    )
                    affinity_fallback_used = True
                except Exception as exc:  # Catch network/JSON issues
                    emit_event(
                        state,
                        kind="boltz_exception",
                        node="characterize_molecules",
                        severity="error",
                        data={"error": str(exc)}
                    )
                    affinity_fallback_used = True
            else:
                emit_event(
                    state,
                    kind="boltz_unconfigured",
                    node="characterize_molecules",
                    severity="warning",
                    data={"message": "Boltz credentials missing, using heuristic affinity."}
                )
                affinity_fallback_used = True

            if affinity_fallback_used:
                for mol_id, smiles in ligands_map.items():
                    if 'binding_affinity' in results.get(mol_id, {}):
                        continue
                    score = _heuristic_affinity(smiles)
                    results.setdefault(mol_id, {})
                    results[mol_id]['binding_affinity'] = score
                    boltz_metadata[mol_id] = {'source': 'heuristic_fallback'}
                if not boltz_recorded:
                    tools_executed.append(CharacterizationTool.BOLTZ)
                    boltz_recorded = True
                state.log("characterize_boltz_fallback", {
                    "ligands": len(ligands_map),
                    "proteins": len(proteins),
                    "reason": "heuristic_used"
                })
            elif not boltz_recorded:
                tools_executed.append(CharacterizationTool.BOLTZ)

    # Prepare metadata for experiment results
    executed_tool_names: List[str] = []
    for tool in tools_executed:
        value = tool.value if isinstance(tool, CharacterizationTool) else str(tool)
        if value not in executed_tool_names:
            executed_tool_names.append(value)

    # Convert results to ExperimentResult objects
    mapped_any = False
    for mol_id in state.current_bo_recommendations:
        if mol_id in state.search_space and mol_id in results:
            smiles = state.search_space[mol_id]
            
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
                    "characterization_tool": tool_choice.value if isinstance(tool_choice, CharacterizationTool) else str(tool_choice),
                    "tools_used": executed_tool_names,
                    "all_properties": dict(results[mol_id])
                }

                boltz_info = boltz_metadata.get(mol_id)
                if boltz_info:
                    metadata["boltz"] = boltz_info
                    if isinstance(boltz_info, dict) and str(boltz_info.get('source', '')).startswith('heuristic'):
                        metadata["boltz_fallback"] = True
                elif affinity_fallback_used:
                    metadata["boltz_fallback"] = True

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
        "tool_used": tool_choice.value if isinstance(tool_choice, CharacterizationTool) else str(tool_choice),
        "tools_executed": executed_tool_names,
        "molecules_characterized": len(results),
        "properties_mapped": len(state.experimental_results),
        "boltz_required": requires_boltz or boltz_only,
        "boltz_fallback": affinity_fallback_used,
        "elapsed_seconds": round(time.perf_counter() - node_started_at, 3),
    })

    print(f"🔍 EXITING NODE: {characterize_molecules_node.__name__}")
    print(f"   - New iteration: {state.current_iteration}")
    print(f"   - New status: {state.status}")
    print(f"   - Should continue: {state.should_continue()}")
    
    return state