---
id: 008
title: Crop Library — versioned crop records, FAO-seeded
type: AFK
labels: [needs-triage]
blocked_by: [001]
user_stories: [10, 11]
phase: 2
---

## What to build

In-app crop knowledge: create/edit crop records (growth stages with durations and water-stress weights; per-country planting windows), every save creating a new attributed version. Seed data: Kenya maize (long rains) from FAO crop calendar sources, entered as the first record. List and detail screens; a crop version is selectable and immutable once referenced by a product.

## Acceptance criteria

- [ ] CRUD screens for crops: stages, durations, stress weights, per-country planting windows
- [ ] Every edit produces a new version with editor attribution; history viewable
- [ ] Kenya maize long-rains record present, marked as FAO-seeded
- [ ] Stage durations must sum to a sane season length (validated, warning not block)
- [ ] Test: version immutability once referenced

## Blocked by

- 001-walking-skeleton
