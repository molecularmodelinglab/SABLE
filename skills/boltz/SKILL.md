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
