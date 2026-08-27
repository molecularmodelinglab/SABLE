import pytest

from nodes.argument_extraction.hybrid import (
    HybridArgumentExtractor,
    _resolve_boltz_objective_targets,
)
from schemas.state import WorkflowState


def _state(provider: str) -> WorkflowState:
    return WorkflowState(
        user_prompt="optimize binding affinity",
        characterization_config={"boltz": {"provider": provider}},
    )


def test_prompt_extraction_keeps_semantic_binding_affinity_intent():
    extractor = HybridArgumentExtractor(configure_components=False)

    result = extractor.extract_with_rules(
        "Optimize C1=CC=CC=C1 for better binding affinity to UniProt P30838"
    )
    target = next(
        item for item in result["target_properties"]
        if item["property_name"] == "binding_affinity"
    )

    assert target["optimization_mode"] == "MIN"
    assert target["bounds"] == (-3.0, 6.0)


@pytest.mark.parametrize(
    ("provider", "expected_name", "expected_mode", "expected_bounds"),
    [
        ("platform", "boltz_optimization_score", "MAX", (0.0, 1.0)),
        ("self_hosted", "binding_affinity", "MIN", (-3.0, 6.0)),
    ],
)
def test_runtime_provider_selects_native_boltz_objective(
    provider,
    expected_name,
    expected_mode,
    expected_bounds,
):
    semantic_targets = [{
        "property_name": "binding_affinity",
        "optimization_mode": "MIN",
        "bounds": (-3.0, 6.0),
        "weight": 1.0,
    }]

    resolved, auto_added = _resolve_boltz_objective_targets(
        semantic_targets,
        _state(provider),
        ensure_primary=True,
    )

    assert auto_added is None
    assert resolved[0]["property_name"] == expected_name
    assert resolved[0]["optimization_mode"] == expected_mode
    assert tuple(resolved[0]["bounds"]) == expected_bounds


def test_protein_target_auto_add_uses_configured_backend_contract():
    resolved, auto_added = _resolve_boltz_objective_targets(
        [],
        _state("platform"),
        ensure_primary=True,
    )

    assert auto_added == "boltz_optimization_score"
    assert resolved[0]["optimization_mode"] == "MAX"
    assert tuple(resolved[0]["bounds"]) == (0.0, 1.0)