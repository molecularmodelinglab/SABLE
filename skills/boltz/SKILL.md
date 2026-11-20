---
name: boltz
description: Submit one self-hosted Boltz affinity prediction for an explicit ligand and protein target through the configured HPC API.
---

Use `predict` with one identified molecule and explicit protein sequences with
unique chain IDs. Service `boltz` binds the self-hosted API origin, optionally with
a credential reference. Boltz Platform screens are provided by `boltz-platform`.

This package preserves the existing `BoltzTool._affinity_yaml` document as JSON
(valid YAML), with a noncolliding ligand chain and explicit affinity binder.
Submit makes one POST `/submit_yaml_text`; poll makes one GET `/jobs/{id}`.
The provider's status route may refresh cached job metadata and download its own
outputs; the callback never launches work or submits a second job. There is no
cancellation API. The skill neither downloads structures nor exposes server paths.

Return affinity only when the provider reports `completed`; an early affinity
field is not completion. Preserve affinity as the model's native prediction,
without labeling it as experimental free energy. Confidence remains separate.
Compare predictions only within the protein scope. UniProt lookup, modified or
cyclic proteins, MSA settings, constraints and templates are unsupported here.

HTTP uses no retries or redirects, a 20-second I/O timeout, a 30-second elapsed
response check and a 4 MB decoded-response limit. Local timeout is not cancellation.

For a library or BayBE batch use `screen` with `source_action_id` pointing to the
recorded library or recommendation and explicit `proteins`. It queues one to 100
independent jobs atomically; the runtime submits and polls them without further
model decisions. Do not ask the user to choose candidate-by-candidate execution.
Every molecule has its own submission lease, remote ID and outcome. An uncertain
submission is never automatically retried. Completed siblings remain recorded.
Use the screen action ID as a BayBE measurement reference after all jobs finish.
Missing or failed scores do not become fabricated observations; incomplete batches
must be reported and resolved before another recommendation.

Use BayBE objective `binding_affinity`, mode `MIN`, for the native
`affinity_pred_value` (log10 micromolar). Confidence is not affinity. Keep the
same exact protein sequences and chain assignments for every round and baseline.
The server does not report its model version; disclose that limitation and keep
the deployment/model configuration fixed during a campaign. Resolve a UniProt
accession using `uniprot` first; no receptor coordinate file is needed.
