---
name: boltz-platform
description: Screen an owned candidate library against explicit protein sequences using Boltz Platform, preserving target-scoped scores and confidence metrics.
---

Use `screen` with a successful `candidate_library` action reference and explicit
protein sequences and unique chain IDs. Maximum 100 molecules. Service
`boltz-platform` uses the Boltz SDK and the active credential selected in the
user's Account settings. Ask the user to select Boltz Platform and an active saved
credential there if it is unavailable. Neither endpoints nor secrets belong in
model inputs. No Platform endpoint or deployment API key is required.

Keep all comparisons within the returned protein scope. `optimization_score` is a
ranking score, not affinity or binding free energy. Its range is not specified by
the SDK. Binding/structure confidence, pTM, ipTM, complex pLDDT and interface pLDDT
are documented by the installed Platform SDK as 0–1. Do not multiply or reinterpret
them as self-hosted metrics. Missing molecules are explicit exclusions. Duplicate,
ambiguous or conflicting identities fail collection. Structures are not downloaded.

The handlers use the SDK streaming interface with automatic retries disabled and
explicitly bounded pagination. The hosted origin is fixed inside the skill so
the self-hosted service's `BOLTZ_BASE_URL` cannot redirect saved Platform keys. The package supports explicit unmodified protein sequences only;
UniProt resolution, templates, custom constraints, workspaces and modified or
cyclic proteins are outside this input contract. SMARTS filtering is disabled as
in the existing adapter so submitted candidates are not silently filtered.

Submit makes one POST. Poll makes at most one status GET and two result-page GETs.
Cancellation makes one stop POST, then at most those three GETs if completion won
the race. Provider `stopped` means cancelled; a pending/running stop response is
not confirmation. There are no retries, redirects, downloads or polling loops.
Each request has 20-second I/O timeout, 30-second elapsed response check and 4 MB
decoded-response limit. A failed page limit or malformed result is an error, never
a silently truncated success. Local timeout does not cancel remote work.

Jobs pin the selected credential ID and a key fingerprint when queued. Account
selection changes apply to new jobs. Deleted, inactive, foreign-owned or replaced
credentials block further provider requests; they never fall back to a deployment
key. A queued job can still be cancelled locally without its credential.

For Bayesian optimization, use `source_action_id` referencing a BayBE
recommendation (or a whole candidate library). The screen evaluates only those
selected candidates and retains the original library identity. Use its completed
action ID in BayBE's cumulative `measurement_action_ids`. `library_action_id`
remains accepted for whole-library screens; supply exactly one source reference.
Use objective `boltz_optimization_score`, mode `MAX`. Do not reuse self-hosted
affinity bounds or assume a 0–1 score range. Confidence is not the objective.
Keep exact protein sequences and chain assignments fixed across rounds. Resolve
UniProt targets through `uniprot` before asking for sequences. Model version is
not reported by this adapter; disclose that limitation.
