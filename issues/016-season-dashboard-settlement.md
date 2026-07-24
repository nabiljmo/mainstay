---
id: 016
title: Season dashboard + settlement computation
type: AFK
labels: [needs-triage]
blocked_by: [015]
user_stories: [29, 30, 34]
phase: 5
---

## What to build

The season watched live: for each published product with bound policies, as each phase's window closes and CHIRPS final data arrives, the Index Engine (same code that priced) computes the phase's actual index and payout per zone. Dashboard shows phase-by-phase progress per zone; preliminary-data estimates clearly labelled provisional; final-data results marked settled. Scheduled job checks for newly available final data.

## Acceptance criteria

- [ ] Dashboard: per product, per zone, per phase — provisional vs final clearly distinguished
- [ ] Settlement values computed only from CHIRPS final; provisional never persists as settlement
- [ ] Scheduled data-availability check runs without manual triggering
- [ ] Same Index Engine call path as pricing (asserted by test, not convention)
- [ ] End-to-end test: synthetic season → known expected phase payouts appear on dashboard

## Blocked by

- 015-bind-policy-register
