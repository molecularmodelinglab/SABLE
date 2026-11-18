---
name: baybe
description: Recommend experimental batches and rank recorded molecular observations with explicit objectives and round limits.
---

Use `recommend` with a HEALER `library_action_id`, objective targets, `batch_size`, `max_rounds`, and all relevant RDKit `measurement_action_ids`. Start with an empty measurement list for an initial design. Never supply invented measurement values or copy values from prose: the runtime resolves recorded observations by reference, enforcing identity, method, units and completeness.

Evaluate the returned recommendation by calling RDKit with `source_action_id` equal to the recommendation action ID. All previous recommendations must have complete objective observations before another batch is requested. Keep cumulative measurement references; leaving out earlier batches is rejected. Stop when `stop_reason` is present, the library is exhausted, or the chosen round limit is reached. The final batch may be smaller than the requested size.

Use `single` for one objective, `pareto` for multiple competing objectives, or `desirability` with explicit increasing bounds and positive weights. `MATCH` requires bounds and targets their midpoint. Desirability bounds normalize each objective before combining; they are not hard feasibility constraints. Objectives, strategy and maximum rounds are fixed after the first recommendation for a library. Do not silently change the optimization problem.

Use `rank` on the same library and cumulative measurement references for the final report. It uses SABLE's existing ranking implementation and reports only measured candidates. Cite the ranking action and relevant measurement actions; do not describe a recommendation as an observed improvement. Comparisons to the starting molecule require a separately recorded baseline measurement. RDKit-derived objectives do not establish potency, safety, or experimental hit-to-lead success.

The local implementation is declared in `manifest.json` and runs through `worker.py`; deployment requirements are checked before this capability is advertised.
