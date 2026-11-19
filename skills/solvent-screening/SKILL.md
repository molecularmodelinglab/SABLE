---
name: solvent-screening
description: Screen supplied solvent candidates against a target using sourced Hansen solubility parameters and an explicit interaction radius.
---

Use for preliminary solvent compatibility screening when the user supplies Hansen dispersion, polar and hydrogen-bonding parameters, their sources, a common temperature, and the target interaction radius. Ask for missing inputs; do not invent parameters from SMILES, substitute logP, or reuse a radius fitted for another target or temperature.

Call `screen` with parameters in MPa^0.5, temperature in kelvin, and any candidate IDs excluded by independently established process constraints. Preserve user-provided parameter sources verbatim; a source label records provenance, not independent verification. All supplied parameters and the radius must apply at the declared temperature. Hard safety, reactivity, boiling-point, cost and regulatory constraints require separate evidence; excluded IDs let the user remove candidates on that basis before ranking.

The operation computes Ra = sqrt(4*(delta_d difference)^2 + (delta_p difference)^2 + (delta_h difference)^2), then RED = Ra / target radius. Smaller RED ranks first. RED below one is inside the supplied sphere, one is on its boundary, and above one is outside. Ties share a distance but appear in candidate-ID order for reproducibility. These labels are a model heuristic, not experimental solubility, selectivity, mixture behavior, kinetics, safety or process validation. The operation handles pure supplied candidates, not solvent mixtures or fitted parameters.

Cite the recorded screening action in the final report, describe exclusions and boundary cases, and state that compatibility needs experimental confirmation. If all candidates are excluded, report that no eligible candidates remain and ask whether constraints should change. Additional characterization can be chosen from other installed skills when it addresses the actual requested endpoint.
