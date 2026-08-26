from tools.boltz.models import BoltzJobStatus, BoltzMetricSet, protein_scope_id
from tools.boltz.normalization import canonical_properties
from tools.boltz.self_hosted import normalize_self_hosted_results


def test_platform_metrics_are_not_mapped_to_binding_affinity():
    properties = canonical_properties(BoltzMetricSet(
        binding_confidence=0.91,
        optimization_score=0.73,
        structure_confidence=0.88,
    ))

    assert properties == {
        "boltz_binding_confidence": 0.91,
        "boltz_optimization_score": 0.73,
        "boltz_structure_confidence": 0.88,
    }
    assert "binding_affinity" not in properties


def test_self_hosted_affinity_keeps_existing_property_name():
    assert canonical_properties(BoltzMetricSet(affinity=-7.4)) == {
        "binding_affinity": -7.4,
    }


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