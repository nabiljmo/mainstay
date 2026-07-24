---
id: 005
title: Admin-snap — align zones to admin level 2 districts
type: AFK
labels: [needs-triage]
blocked_by: [003]
user_stories: [9]
phase: 1
---

## What to build

An optional toggle on the zoning form: when enabled, after clustering each admin level 2 district (GADM boundaries, fetched and cached like weather data) is assigned wholly to the cluster covering most of its area, so zone boundaries follow district lines. The map shows district-aligned zones; homogeneity scores are recomputed for the snapped zones so the actuary sees the cost of snapping.

## Acceptance criteria

- [x] Toggle on the zoning form; off by default
- [x] GADM level 2 boundaries fetched from source on first use and cached
- [x] Every district lands in exactly one zone; no orphan or split districts
- [x] Homogeneity scores shown for snapped zones (comparable against the unsnapped run)
- [x] Test: synthetic grid + fake districts → majority assignment verified

## Blocked by

- 003-zoning-run
