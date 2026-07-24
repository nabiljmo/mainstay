---
id: 004
title: Zone map approval — freeze a named version
type: AFK
labels: [needs-triage]
blocked_by: [003]
user_stories: [8]
phase: 1
---

## What to build

The approval gate: from a draft zoning run, the actuary clicks Approve, gives it a name (e.g. KEN-v1), and the zone map freezes — immutable polygons, parameters, data years, and an audit record (who, when). Approved versions are listed and viewable; drafts can be discarded. Everything downstream (products, quotes) will reference approved versions only.

## Acceptance criteria

- [x] Approve action freezes the run into a named, read-only zone map version with full audit record
- [x] Approved versions list shows name, country, parameters, data years, approver, date
- [x] An approved version's polygons and scores render on the map identically to the draft they came from
- [x] Attempting to modify an approved version fails at the API level
- [x] Tests: approval immutability, audit record completeness

## Blocked by

- 003-zoning-run
