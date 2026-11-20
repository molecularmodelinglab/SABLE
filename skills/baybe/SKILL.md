---
name: baybe
description: Recommend experimental batches and rank recorded molecular observations with explicit objectives and round limits.
---

Use `recommend` with a HEALER `library_action_id`, objective targets, `batch_size`, `max_rounds`, and all relevant recorded `measurement_action_ids`. Start with an empty measurement list for an initial design. Never supply invented measurement values or copy values from prose: the runtime resolves recorded observations by reference, enforcing identity, method, units and completeness.

Both `batch_size` and `max_rounds` support values from 1 to 100. Recommendation and ranking inputs accept up to 100 cumulative measurement references. Campaign action/unit budgets and available library candidates still constrain execution.

Evaluate the returned recommendation by calling the objective-appropriate scoring operation with `source_action_id` equal to the recommendation action ID. All previous recommendations must have complete objective observations before another batch is requested. Keep cumulative measurement references; leaving out earlier batches is rejected. Stop when `stop_reason` is present, the library is exhausted, or the chosen round limit is reached. The final batch may be smaller than the requested size.

Use `single` for one objective, `pareto` for multiple competing objectives, or `desirability` with explicit increasing bounds and positive weights. `MATCH` requires bounds and targets their midpoint. Desirability bounds normalize each objective before combining; they are not hard feasibility constraints. Objectives, strategy and maximum rounds are fixed after the first recommendation for a library. Do not silently change the optimization problem.

Use `rank` on the same library and cumulative measurement references for the final report. It uses SABLE's existing ranking implementation and reports only measured candidates. Cite the ranking action and relevant measurement actions; do not describe a recommendation as an observed improvement. Comparisons to the starting molecule require a separately recorded baseline measurement. RDKit-derived objectives do not establish potency, safety, or experimental hit-to-lead success.

The local implementation is declared in `manifest.json` and runs through `worker.py`; deployment requirements are checked before this capability is advertised.

Target-specific predictions are also supported. For self-hosted Boltz use
`binding_affinity` with `MIN`; for Boltz Platform use `boltz_optimization_score`
with `MAX`. These are different metrics and must not be substituted for one
another or merged into a common affinity value. Do not assume bounds for Platform
scores. Use `single` without normalization bounds for a single score objective.
Evaluate each recommendation with the available Boltz skill's `screen`, passing
its action ID as `source_action_id` and the same explicit proteins each time.
Pass completed screen action IDs to subsequent recommendation/ranking operations.
The runtime resolves native provider outputs into observations, validates molecule
and library identity, and rejects mixed targets and incompatible provenance.
Both backends support batches; self-hosted batches consist of independently
tracked molecule jobs. Missing scores prevent advancing to the next round; report
failures instead of treating confidence or a descriptor as an affinity score.
