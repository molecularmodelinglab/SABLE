---
name: healer
description: Enumerate analogues with local HEALER while preserving explicit scaffold and descriptor constraints.
---

Use `enumerate` once to create a bounded candidate library from explicit starting SMILES. The result action ID is the library reference for RDKit and BayBE; do not retype the generated structures.

Set `scaffold_smarts` when a substructure must be retained and `property_filters` for hard descriptor bounds. These filters are applied in code; optimizer target bounds are a separate concept. The scaffold must occur in the starting molecule. If the requested protected scaffold is ambiguous, ask for it before enumeration. Record the chosen scaffold in the report.

`max_candidates` caps raw products, so the valid, unique, constrained library may be smaller or empty. The starting molecule is excluded from the candidate pool and can be evaluated separately as a baseline. An empty library is a useful negative result, not a reason to fabricate candidates or relax constraints silently.

MoleculeHEALER is the default. SiteHEALER requires explicit zero-based RDKit atom indices in `reactive_sites`; do not guess atom numbering. FragmentHEALER is available for compatible fragments. Reaction tags restrict the reaction families; stock selection and data paths belong to deployment configuration, not model arguments. Local operations have a 120-second limit; remote enumeration jobs are not supported by this skill yet.

The retained reaction rows provide computational enumeration provenance. They are not proof of experimental synthetic success, commercial availability, or biological activity.

The local implementation is declared in `manifest.json` and runs through `worker.py`; deployment requirements are checked before this capability is advertised.

Enumeration breadth and evaluation budget are independent. Prefer
`max_total_products` for the raw library cap; `max_candidates` remains an alias,
not a promised final yield. Do not derive it from batch size × rounds or choose a
small cap just because only a few predictions are requested. When the user has
not specified enumeration breadth, omit `max_total_products`, `n_compositions`
and `max_bbs_per_frag` to use deployment defaults: normally 8000 raw products,
100 compositions and 10 building blocks per fragment. These settings are bounded
by deployment limits. Respect explicit user limits instead of overriding them.
A composition is a search decomposition, not a generated molecule.

Generate the library once, then pass its action reference to BayBE. Evaluate only
the recommendation batches (e.g. two rounds of three scores), not the entire
library. Whole-library scoring operations retain their separate batch limits.
The `enumeration` summary reports raw source count, candidates considered,
backend-filtered rows, duplicate/seed/scaffold/property exclusions, and usable
library size. `capacity_reached` means the raw cap was reached, not that more valid
analogues are guaranteed. Do not relax constraints or rerun with a different seed
to manufacture a requested yield. The local adapter retains its similarity filter
for non-test stocks and its 120-second worker timeout; large searches may require
the API skill.
