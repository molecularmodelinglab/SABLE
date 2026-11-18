---
name: boltz-platform
description: Screen an owned candidate library against explicit protein sequences using Boltz Platform, preserving target-scoped scores and confidence metrics.
---

Use `screen` with a successful `candidate_library` action reference and explicit
protein sequences and unique chain IDs. Maximum 100 molecules. Service
`boltz-platform` binds the Platform origin (normally https://api.boltz.bio) and a
credential reference. Neither endpoints nor secrets belong in model inputs.

Keep all comparisons within the returned protein scope. `optimization_score` is a
ranking score, not affinity or binding free energy. Its range is not specified by
the SDK. Binding/structure confidence, pTM, ipTM, complex pLDDT and interface pLDDT
are documented by the installed Platform SDK as 0–1. Do not multiply or reinterpret
them as self-hosted metrics. Missing molecules are explicit exclusions. Duplicate,
ambiguous or conflicting identities fail collection. Structures are not downloaded.

The handlers reproduce `tools/boltz/platform.py` request and metric semantics using
the verified SDK REST routes, avoiding its automatic retries and unbounded
pagination. The package supports explicit unmodified protein sequences only;
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
