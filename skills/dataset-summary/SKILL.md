---
name: dataset-summary
description: Summarize a finite numeric observation series with its count, minimum, maximum, and mean.
---

Use `summarize` for descriptive statistics on observations already known to share a method and unit. Preserve the supplied unit. Do not combine measurements across incompatible conditions or interpret these descriptive statistics as predictive uncertainty.

The operation requires at least one finite number. Missing values must be resolved or excluded explicitly before calling it. It performs no model inference or remote requests.
