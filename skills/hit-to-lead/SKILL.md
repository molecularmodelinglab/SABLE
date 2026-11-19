---
name: hit-to-lead
description: Plan bounded constrained analogue exploration using HEALER, RDKit descriptors and Bayesian optimization.
---

Use this workflow when a user wants to explore or optimize analogues. It composes installed operations and has no executable operation of its own.

Establish explicit starting SMILES, objectives, preserved scaffold or sites, hard property constraints, and a small evaluation budget. Clarify unsupported activity/solubility claims rather than substituting convenient descriptor proxies. Load an available enumeration skill (`healer` for local execution or `healer-api` for the configured service), RDKit and BayBE for their contracts. Remote job receipts are pending work; use the resolved candidate library after the runtime resumes the campaign.

A typical campaign is:

1. Evaluate the starting molecule separately if improvement will be reported.
2. Enumerate a constrained library once with HEALER. Keep the original constraints and library reference throughout optimization.
3. Ask BayBE for a small initial batch with no measurements; evaluate that recommendation using RDKit's `source_action_id` input.
4. Repeat recommendation and evaluation using cumulative measurement action references. Let validated observations inform the next batch. Choose at most four rounds and respect the campaign's remaining tool budget.
5. Call the BayBE `rank` operation with all measurement action references, then cite its resulting action in `finish.evidence`. Report best values or the Pareto front, failures, exclusions, actual evaluations, constraints, and the difference between computational descriptors and experimental evidence. A prose ranking alone does not produce the structured report needed by the UI.

One to three rounds with a batch of two or three is a useful first campaign. Every HEALER call costs two operation units; each RDKit or BayBE call costs one. Loading instructions and clarifications consume model decisions. Reserve an operation for final ranking and a model decision for the report. Do not enumerate repeatedly to evade a library's fixed round limit.

If the constrained library is empty, explain that outcome and ask whether the user wants to revise constraints. If no installed tool measures a requested endpoint, state the gap and ask how to proceed. The workflow does not require a fixed sequence when the user's task only needs a subset of these capabilities.

For a named starting compound, load the available `name-to-smiles` skill and resolve the name before asking the user for SMILES. Use the exact returned structure only for a single resolved match; retain its PubChem CID and source action as evidence. Ask for clarification for ambiguous matches, unresolved names, or a salt/mixture that the next operation cannot accept. Never infer stereochemistry or strip components silently.
