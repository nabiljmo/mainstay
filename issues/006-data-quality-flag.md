---
id: 006
title: Data-quality flag — history depth traffic light
type: AFK
labels: [needs-triage]
blocked_by: [002]
user_stories: [4]
phase: 1
---

## What to build

The traffic-light flag wherever a year range is chosen (zoning and, later, pricing): green ≥15 years, amber 10–14, red <10 with the wording "short history: tail risk may be underestimated". Advisory only — never blocks. The flag value is stored on zoning runs now and travels onto product records when pricing exists.

## Acceptance criteria

- [x] Flag renders live as the year range changes on the zoning form
- [x] Red flag carries the tail-risk wording; nothing is ever blocked
- [x] Flag value persisted on the zoning run record (and later on products)
- [x] Test: boundary years (9/10, 14/15) map to the correct colour

## Blocked by

- 002-chirps-fetch-cache
