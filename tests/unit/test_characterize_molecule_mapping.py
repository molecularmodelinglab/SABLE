from nodes.characterize_molecules import _map_target_properties
from schemas.state import TargetProperty


def test_platform_score_is_not_mapped_to_binding_affinity():
    targets = [TargetProperty(name="binding_affinity")]

    mapped = _map_target_properties(
        targets,
        {"boltz_optimization_score": 0.82},
    )

    assert mapped == {}


def test_platform_score_maps_to_resolved_platform_target():
    targets = [TargetProperty(name="boltz_optimization_score")]

    mapped = _map_target_properties(
        targets,
        {"boltz_optimization_score": 0.82},
    )

    assert mapped == {"boltz_optimization_score": 0.82}