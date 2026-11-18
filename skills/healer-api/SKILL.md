---
name: healer-api
description: Enumerate reaction-based analogues through the remote HEALER API with durable jobs and explicit scaffold and descriptor constraints.
---

Use `enumerate` with explicit starting SMILES to submit a remote enumeration. The returned `remote_job` is a receipt, not a candidate library. Wait for the runtime to resolve the job before using its candidate-library result with downstream skills. Do not resubmit simply because a job is running. The runtime polls every five seconds with a 900-second job limit.

Set `scaffold_smarts` to preserve a substructure and `property_filters` for hard descriptor bounds. The scaffold must occur in the starting molecule; ask for clarification when the protected scaffold is ambiguous. Results use the same canonicalization, identity, starting-molecule exclusion, deduplication, and constraint filtering as the local HEALER library. `max_candidates` caps raw products before filtering, so the final library may be smaller or empty. Do not silently relax constraints.

MoleculeHEALER is the default. SiteHEALER requires explicit zero-based RDKit atom indices for the supplied SMILES in `reactive_sites`; do not guess numbering. FragmentHEALER is unsupported by this API and is rejected. The API does not accept the requested seed, so provenance records `seed_applied: false`; do not claim deterministic seed replay. Cancellation is not exposed by this package. A server-revoked job is reported as cancelled.

The deployment supplies the `healer-api` service endpoint and optional bearer credential. Stock comes from deployment configuration (`HEALER_STOCK`, default `US_stock`). This package needs RDKit and requests, but no local HEALER installation or building-block data. The API contract and status translation are implemented in `remote.py`; the manifest declares the durable job and schemas.

Retained reaction rows, API statistics, and actual remote job identity provide computational provenance, not evidence of experimental synthesis, commercial availability, or biological activity.
