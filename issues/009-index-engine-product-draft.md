---
id: 009
title: Index Engine + product draft — phases proposed, historical index visible
type: AFK
labels: [needs-triage]
blocked_by: [004, 008]
user_stories: [12, 13, 14, 15]
phase: 2
---

## What to build

The heart. The Index Engine as a pure module: product definition + rainfall series in → per-phase, per-year index values and payouts out, for all three cover types (deficit, excess, continuous dry-day) with linear strike/exit payouts. Phase start is a swappable rule (fixed calendar in v1). On top of it, the product designer screen: pick approved zone map + crop version → system proposes phases from the planting window and stage durations, a cover type per phase, percentile-based starting triggers, and stress-weighted phase limits — all editable — and shows each zone's historical index values per phase so the actuary sees what the product would have done every year.

## Acceptance criteria

- [ ] Index Engine computes all three cover types correctly on synthetic series (golden tests, hand-computed)
- [ ] Property tests: payout monotonic in deficit; never exceeds phase limit; strike/exit boundary behaviour exact
- [ ] Product designer proposes phases, triggers, and limits from crop + zone history; every field editable
- [ ] Historical per-phase index and payout table visible per zone before any pricing
- [ ] Engine is date-agnostic: same call path works on any year range (settlement-ready)
- [ ] Missing-day handling defined and tested

## Blocked by

- 004-zone-approval-versioning
- 008-crop-library
