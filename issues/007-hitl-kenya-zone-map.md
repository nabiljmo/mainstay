---
id: 007
title: "HITL: Kenya zone map reviewed and approved (KEN-v1)"
type: HITL
labels: [needs-triage]
blocked_by: [003, 004, 005, 006]
user_stories: [5, 6, 7, 8]
phase: 1
---

## What to build

Nothing new — this is the Phase 1 milestone, done by a human. The actuary (acting actuary) runs zoning for Kenya on full-depth CHIRPS, iterates on cluster count and sensitivity while watching homogeneity scores, decides on admin-snap, and approves KEN-v1. Output is the first production artefact of the platform.

## Acceptance criteria

- [x] Kenya CHIRPS fetched at the chosen depth (flag noted)
- [x] At least two candidate runs compared before choosing
- [x] KEN-v1 approved and frozen with audit record
- [x] Chosen parameters and the reasoning noted in the version record

## Blocked by

- 003-zoning-run
- 004-zone-approval-versioning
- 005-admin-snap
- 006-data-quality-flag
