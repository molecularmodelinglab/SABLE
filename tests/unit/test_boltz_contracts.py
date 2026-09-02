from tools.boltz.models import BoltzJobStatus, BoltzMetricSet, protein_scope_id
from tools.boltz.normalization import canonical_properties
from tools.boltz.self_hosted import normalize_self_hosted_results


def test_platform_metrics_are_not_mapped_to_binding_affinity():
    properties = canonical_properties(BoltzMetricSet(
        binding_confidence=0.91,
        optimization_score=0.73,
        structure_confidence=0.88,
        ptm=0.84,
        iptm=0.86,
        complex_plddt=0.79,
        complex_iplddt=0.81,
    ))

    assert properties == {
        "boltz_binding_confidence": 0.91,
        "boltz_optimization_score": 0.73,
        "boltz_structure_confidence": 0.88,
        "boltz_ptm": 0.84,
        "boltz_iptm": 0.86,
        "boltz_complex_plddt": 0.79,
        "boltz_complex_iplddt": 0.81,
    }
    assert "binding_affinity" not in properties


def test_self_hosted_affinity_keeps_existing_property_name():
    assert canonical_properties(BoltzMetricSet(affinity=-7.4)) == {
        "binding_affinity": -7.4,
    }


def test_self_hosted_normalization_preserves_confidence_and_cif_path():
    normalized = normalize_self_hosted_results({
        "ligand-1": {
            "job_id": "job-1",
            "affinity": -7.4,
            "confidence": 0.82,
            "cif_path": "artifacts/boltz_cifs/job-1.cif",
        },
    }, "scope-1")

    assert normalized[0].metrics.binding_confidence == 0.82
    assert normalized[0].structure_path == "artifacts/boltz_cifs/job-1.cif"


def test_self_hosted_normalization_does_not_guess_numeric_fields():
    normalized = normalize_self_hosted_results({
        "ligand-1": {
            "job_id": "job-1",
            "affinity": {"unrelated_score": 0.95},
        },
    }, "scope-1")

    assert normalized[0].status == BoltzJobStatus.FAILED
    assert normalized[0].metrics.affinity is None


def test_protein_scope_is_stable_across_input_order():
    first = [
        {"chain_id": "B", "sequence": "CCCC"},
        {"chain_id": "A", "sequence": "AAAA"},
    ]
    second = list(reversed(first))

    assert protein_scope_id(first) == protein_scope_id(second)


def test_protein_scope_changes_with_target():
    first = [{"chain_id": "A", "sequence": "AAAA"}]
    second = [{"chain_id": "A", "sequence": "AAAC"}]

    assert protein_scope_id(first) != protein_scope_id(second)