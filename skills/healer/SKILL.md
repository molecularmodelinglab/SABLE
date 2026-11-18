---
name: healer
description: Enumerate analogues with local HEALER while preserving explicit scaffold and descriptor constraints.
---

Use `enumerate` once to create a bounded candidate library from explicit starting SMILES. The result action ID is the library reference for RDKit and BayBE; do not retype the generated structures.

Set `scaffold_smarts` when a substructure must be retained and `property_filters` for hard descriptor bounds. These filters are applied in code; optimizer target bounds are a separate concept. The scaffold must occur in the starting molecule. If the requested protected scaffold is ambiguous, ask for it before enumeration. Record the chosen scaffold in the report.

`max_candidates` caps raw products, so the valid, unique, constrained library may be smaller or empty. The starting molecule is excluded from the candidate pool and can be evaluated separately as a baseline. An empty library is a useful negative result, not a reason to fabricate candidates or relax constraints silently.

MoleculeHEALER is the default. SiteHEALER requires explicit zero-based RDKit atom indices in `reactive_sites`; do not guess atom numbering. FragmentHEALER is available for compatible fragments. Reaction tags restrict the reaction families; stock selection and data paths belong to deployment configuration, not model arguments. Local operations have a 120-second limit; remote enumeration jobs are not supported by this skill yet.

The retained reaction rows provide computational enumeration provenance. They are not proof of experimental synthetic success, commercial availability, or biological activity.
