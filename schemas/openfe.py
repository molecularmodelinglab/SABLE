"""OpenFE objective names and unambiguous protocol intent shared by workflow adapters."""

import re

OPENFE_PROPERTIES = {
    "abfe": "openfe_absolute_binding_free_energy",
    "rbfe": "openfe_relative_binding_free_energy",
    "septop": "openfe_septop",
}


def prompt_protocol(prompt: str, *, infer_relative: bool = True) -> str | None:
    patterns = {
        "abfe": r"\babfe\b|\babsolute\s+(?:binding\s+)?free[\s-]+energ(?:y|ies)\b|\bopenfe_absolute_binding_free_energy\b",
        "rbfe": r"\brbfe\b|\bopenfe_relative_binding_free_energy\b",
        "septop": r"\bsep[\s-]?top\b|\bseparated[\s-]+topolog(?:y|ies)\b|\bopenfe_septop\b",
    }
    matches = [protocol for protocol, pattern in patterns.items() if re.search(pattern, prompt, re.I)]
    if len(matches) > 1:
        raise ValueError("Select one OpenFE protocol per run: ABFE, RBFE, or SepTop.")
    if matches:
        return matches[0]
    if infer_relative and re.search(r"\brelative\s+(?:binding\s+)?free[\s-]+energ(?:y|ies)\b", prompt, re.I):
        return "rbfe"
    return None


def route_openfe_targets(targets: list[dict], prompt: str) -> list[dict]:
    """Explicit protocol intent wins over generic binding/OpenFE substring matches."""
    protocol = prompt_protocol(prompt)
    if protocol is None:
        return targets
    separate_affinity = re.search(
        r"\b(?:and|also|plus)\s+(?:better\s+|higher\s+|lower\s+)?(?:binding\s+)?affinity\b"
        r"|\baffinity\s+(?:and|plus)\b", prompt, re.I,
    )
    filtered = [
        dict(target) for target in targets
        if target.get("property_name") not in OPENFE_PROPERTIES.values()
        and (target.get("property_name") not in {"binding_affinity", "affinity"} or separate_affinity)
    ]
    source = next((target for target in targets if target.get("property_name") == OPENFE_PROPERTIES[protocol]), {})
    filtered.append({
        **source,
        "property_name": OPENFE_PROPERTIES[protocol], "optimization_mode": "MIN",
        "bounds": [-30.0, 10.0] if protocol == "abfe" else [-20.0, 20.0],
        "transformation": "LINEAR",
    })
    return filtered
