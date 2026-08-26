from typing import Dict

from tools.boltz.models import BoltzMetricSet


def canonical_properties(metrics: BoltzMetricSet) -> Dict[str, float]:
    """Map only semantically equivalent Boltz metrics to SABLE properties."""

    properties: Dict[str, float] = {}
    mappings = {
        "affinity": "binding_affinity",
        "binding_confidence": "boltz_binding_confidence",
        "optimization_score": "boltz_optimization_score",
        "structure_confidence": "boltz_structure_confidence",
    }
    for metric_name, property_name in mappings.items():
        value = getattr(metrics, metric_name)
        if value is not None:
            properties[property_name] = float(value)
    return properties