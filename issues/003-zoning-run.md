---
id: 003
title: Zoning Engine — run clustering, see zones on the map
type: AFK
labels: [needs-triage]
blocked_by: [002]
user_stories: [5, 6, 7, 34]
phase: 1
---

## What to build

End-to-end zoning: actuary sets country, years, cluster count, and rainfall sensitivity in the UI; a background job computes features from cached CHIRPS (seasonal totals and inter-annual variability, not just means), clusters, and stores zone polygons with per-zone homogeneity scores. The map screen renders the zones coloured by cluster with scores visible. Runs are repeatable — same inputs and seed produce identical zones — and re-running with new parameters creates a new draft run, never overwriting a previous one.

## Acceptance criteria

- [x] Zoning form (country, years, cluster count, sensitivity) starts a job with progress; result appears on the map without a page reload
- [x] Each zone shows a homogeneity score (within-zone rainfall correlation) on hover/click
- [x] Multiple draft runs coexist; the actuary can flip between them
- [x] Reproducibility test: identical inputs + seed → identical zone assignments
- [x] Cross-check test: engine agrees with the original R-script method when configured with its exact features and parameters on shared input
- [x] Coordinate scaling is joint (no independent x/y standardisation distortion)

## Blocked by

- 002-chirps-fetch-cache
