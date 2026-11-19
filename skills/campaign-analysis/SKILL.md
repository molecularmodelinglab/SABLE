---
name: campaign-analysis
description: Analyze recorded molecular observations and produce reproducible figures.
---

Use `analyze` for an explicitly useful analysis or exportable figure. The UI already renders ordinary results, tables, molecule drawings, and descriptor distributions without a tool call. Do not consume the campaign budget merely to make an existing result visible.

Pass `measurement_action_ids` from this campaign, a recorded `property`, `chart` (`distribution`, `progress`, or `chemical_space`), and optimization `direction` (`MIN` or `MAX`; used for progress). Never retype measurements. Sources must use the same method, version, source classification, and unit, and may contain at most 1000 nonduplicated molecules. Missing values and incompatible sources are rejected, not imputed.

Progress requires observations with candidate/library identity that match recorded recommendations. Its x-axis is the scientific optimization round, never the model decision count. It reuses the shared objective-progress calculation. No estimated optimum or inferred experimental improvement is reported.

Chemical space uses deterministic PCA of the shared Morgan fingerprints (radius 2, 2048 bits), with at least three molecules. It is a two-dimensional projection, not evidence of activity or a guarantee that neighbors are equivalent. State the projection and its limits.

The result includes summary statistics, source action IDs, a portable presentation of charts/tables, and a downloadable PNG. Cite the source observations and analysis action in the report. This operation produces figures; it does not start new experiments or alter objectives.
