---
name: hit-to-lead
description: Plan constrained analogue exploration using enumeration, objective-specific scoring and Bayesian optimization.
---

Use this workflow when a user wants to explore or optimize analogues. It composes installed operations and has no executable operation of its own.

Establish explicit starting SMILES, objectives, preserved scaffold or sites, hard property constraints, and a small evaluation budget. Clarify unsupported activity/solubility claims rather than substituting convenient descriptor proxies. Load an available enumeration skill (`healer` for local execution or `healer-api` for the configured service), the relevant scoring skill and BayBE for their contracts. Remote job receipts are pending work; use the resolved candidate library after the runtime resumes the campaign.

A typical campaign is:

1. Evaluate the starting molecule separately if improvement will be reported.
2. Enumerate a constrained library once with HEALER. Keep the original constraints and library reference throughout optimization.
3. Ask BayBE for a small initial batch with no measurements; evaluate that recommendation using the selected scoring skill's `source_action_id` input (RDKit for descriptors, Boltz for binding predictions).
4. Repeat recommendation and evaluation using cumulative measurement action references. Let validated observations inform the next batch. Preserve the user's requested round count within installed operation limits and the campaign's remaining tool budget.
5. Call the BayBE `rank` operation with all measurement action references, then cite its resulting action in `finish.evidence`. Report best values or the Pareto front, failures, exclusions, actual evaluations, constraints, and the difference between computational descriptors and experimental evidence. A prose ranking alone does not produce the structured report needed by the UI.

One to three rounds with a batch of two or three is a useful first campaign. Every HEALER call costs two operation units; each RDKit or BayBE call costs one. Loading instructions and clarifications consume model decisions. Reserve an operation for final ranking and a model decision for the report. Do not enumerate repeatedly to evade a library's fixed round limit.

If the constrained library is empty, explain that outcome and ask whether the user wants to revise constraints. If no installed tool measures a requested endpoint, state the gap and ask how to proceed. The workflow does not require a fixed sequence when the user's task only needs a subset of these capabilities.

For a named starting compound, load the available `name-to-smiles` skill and resolve the name before asking the user for SMILES. Use the exact returned structure only for a single resolved match; retain its PubChem CID and source action as evidence. Ask for clarification for ambiguous matches, unresolved names, or a salt/mixture that the next operation cannot accept. Never infer stereochemistry or strip components silently.

For binding optimization, load the available Boltz scoring contract and BayBE
before declaring the workflow unavailable. Resolve a UniProt target using the
`uniprot` skill; both Boltz backends accept sequences without receptor coordinates.
Use the configured available backend; if both are usable and no preference is
known, clarify the provider because scores and costs differ. Preserve the user's
batch size and iteration count. Enumerate once, ask BayBE for the first batch,
call Boltz `screen` with that recommendation's `source_action_id`, wait for the
runtime to collect results, then recommend the next batch using cumulative screen
references. Rank using the same objective after the requested number of rounds.
Self-hosted scoring uses `binding_affinity` MIN (log10 micromolar); Platform uses
`boltz_optimization_score` MAX (a provider ranking score, not affinity). Do not
reuse self-hosted score bounds for Platform or claim actual measured affinity.
For a single objective no normalization bounds are needed. A requested prediction
campaign authorizes these normal evaluation steps: do not ask whether to score
candidates individually when the batch operation exists. Ask only for genuinely
missing context, credentials, unavailable capabilities, or consequential choices.
Do not replace binding objectives with QED/logP. Explicit OpenFE/free-energy
requests require the appropriate OpenFE contract and inputs; do not substitute
Boltz scores for binding free energies.

Library breadth is separate from scoring budget. Preserve requested batch size
and iterations for BayBE; do not use their product as the HEALER library cap.
When enumeration size was not specified, omit the enumeration breadth parameters
to use the deployment's HEALER defaults. A two-round, three-per-round campaign
normally explores a broader library and scores at most six recommended analogues,
plus a separately authorized or explained baseline. Pass library references to
BayBE and recommendation references to scorers so large libraries do not exceed
scorer batch limits. If too few eligible candidates remain, report the generated,
excluded and retained counts and the resulting smaller evaluation budget.
