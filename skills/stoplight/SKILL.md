---
name: stoplight
description: Calculate Stoplight molecular properties through the configured service with explicit molecule identities and partial-result exclusions.
---

Use `characterize` with uniquely identified molecules and optional decimal precision.
The operation queues a durable job; only its submit handler contacts the service.
Service `stoplight` is an origin; the handler appends `/smiles-csv`.

The API returns synchronous CSV and has no remote identifier, polling, cancellation,
or safe lookup after an ambiguous submission. Do not automatically resubmit an
unknown outcome. This job is not reconcilable to a remote identifier.

Use only completed job measurements. Preserve property names: water solubility is
mg/L, molecular weight is the service's molecular-weight property, and interference
and aggregation scores are not experimental assay measurements. Missing/nonfinite
values are omitted, never replaced by zero. Rows without finite HBD are excluded;
identity/order or row-count mismatches fail the batch. SMILES must match the CSV
exactly: this deliberately rejects unverifiable canonicalization or reordered rows.

Transport has no retries or redirects, a 20-second I/O timeout, a 30-second elapsed
response check, and a 4 MB decoded-response limit. Submit makes one request.
