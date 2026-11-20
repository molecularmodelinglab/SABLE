---
name: healer-api
description: Enumerate reaction-based analogues through the remote HEALER API with durable jobs and explicit scaffold and descriptor constraints.
---

Use `enumerate` with explicit starting SMILES to submit a remote enumeration. The returned `remote_job` is a receipt, not a candidate library. Wait for the runtime to resolve the job before using its candidate-library result with downstream skills. Do not resubmit simply because a job is running. The runtime polls every five seconds with a 900-second job limit.

Set `scaffold_smarts` to preserve a substructure and `property_filters` for hard descriptor bounds. The scaffold must occur in the starting molecule; ask for clarification when the protected scaffold is ambiguous. Results use the same canonicalization, identity, starting-molecule exclusion, deduplication, and constraint filtering as the local HEALER library. `max_candidates` caps raw products before filtering, so the final library may be smaller or empty. Do not silently relax constraints.

MoleculeHEALER is the default. SiteHEALER requires explicit zero-based RDKit atom indices for the supplied SMILES in `reactive_sites`; do not guess numbering. FragmentHEALER is unsupported by this API and is rejected. The API does not accept the requested seed, so provenance records `seed_applied: false`; do not claim deterministic seed replay. Cancellation is not exposed by this package. A server-revoked job is reported as cancelled.

The deployment supplies the `healer-api` service endpoint and optional bearer credential. Stock comes from deployment configuration (`HEALER_STOCK`, default `US_stock`). This package needs RDKit and requests, but no local HEALER installation or building-block data. The API contract and status translation are implemented in `remote.py`; the manifest declares the durable job and schemas.

Retained reaction rows, API statistics, and actual remote job identity provide computational provenance, not evidence of experimental synthesis, commercial availability, or biological activity.

Keep library generation separate from downstream evaluation batch size and rounds.
Use `max_total_products` for the raw product cap; `max_candidates` is a compatibility
alias. Do not supply both. A user asking for two rounds of three predictions has
not requested a six-molecule library. Unless the user specifies enumeration
breadth, omit `max_total_products`, `n_compositions` and `max_bbs_per_frag`: the
host normally supplies 8000 raw products, 100 compositions and 10 building blocks
per fragment. The deployment may override these defaults and its product ceiling.
Compositions describe search breadth, not molecule count. The API receives these
settings directly as `max_total_products`, `n_compositions`, `max_bbs_per_frag`.

Generate the library once and let BayBE select evaluation batches by action
reference. Score the recommendation, not the entire library. Report the returned
`enumeration` counts: source rows, retained rows, exclusions by reason, any
truncation, and eligible analogues. A raw cap is not a guaranteed usable yield;
never remove user constraints to inflate it. The API's complete result response
is bounded to 32 MB; oversize responses fail explicitly rather than producing a
silently partial library. Provider-side exclusions not present in `complete`
cannot be inferred; provider statistics remain available in provenance.
