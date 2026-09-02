from typing import Any, Dict, List

from tools.boltz.models import (
    BoltzJobStatus,
    BoltzMetricSet,
    BoltzMoleculeResult,
)


def normalize_self_hosted_results(
    per_ligand: Dict[str, Any],
    protein_scope_id: str,
) -> List[BoltzMoleculeResult]:
    """Normalize the legacy self-hosted response without guessing metric meanings."""

    results: List[BoltzMoleculeResult] = []
    for molecule_id, raw_info in per_ligand.items():
        info = raw_info if isinstance(raw_info, dict) else {}
        raw_affinity = info.get("affinity")
        affinity_value = (
            raw_affinity.get("affinity_pred_value")
            if isinstance(raw_affinity, dict)
            else raw_affinity
        )
        raw_confidence = info.get("confidence")
        confidence_value = (
            raw_confidence.get("confidence_score")
            if isinstance(raw_confidence, dict)
            else raw_confidence
        )
        succeeded = isinstance(affinity_value, (int, float))
        results.append(BoltzMoleculeResult(
            molecule_id=molecule_id,
            protein_scope_id=protein_scope_id,
            provider_result_id=info.get("job_id"),
            status=BoltzJobStatus.SUCCEEDED if succeeded else BoltzJobStatus.FAILED,
            metrics=BoltzMetricSet(
                affinity=float(affinity_value) if succeeded else None,
                binding_confidence=(
                    float(confidence_value)
                    if isinstance(confidence_value, (int, float))
                    else None
                ),
            ),
            structure_path=info.get("cif_file") or info.get("cif_path"),
            sanitized_error=None if succeeded else "No affinity score was returned.",
        ))
    return results