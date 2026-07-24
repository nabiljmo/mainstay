---
id: 017
title: Payout run — review, release, export file
type: AFK
labels: [needs-triage]
blocked_by: [016]
user_stories: [31, 32]
phase: 5
---

## What to build

Season close: once all phases are settled on final data, operations opens the payout run — totals, farmer count, largest single amounts, per-zone table, index evidence, and an anomaly banner on any zone paying >3× its priced expected loss. One human clicks Release; the system exports the payout file (phone, amount, policy number, zone, index evidence reference) for existing payment rails, marks policies settled, and locks the run with an audit record. One payment per farmer.

## Acceptance criteria

- [ ] Payout run screen with all review elements and anomaly flags
- [ ] Release requires explicit confirmation; run locks after release with audit record
- [ ] Exported file contains phone, amount, policy, zone, evidence reference; format documented
- [ ] Policies flip to settled; a farmer appears exactly once in the file
- [ ] End-to-end test: synthetic season → expected file contents byte-for-byte; anomaly path covered

## Blocked by

- 016-season-dashboard-settlement
