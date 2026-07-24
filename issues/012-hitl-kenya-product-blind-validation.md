---
id: 012
title: "HITL: Kenya maize long-rains product published + blind rate comparison"
type: HITL
labels: [needs-triage]
blocked_by: [007, 011]
user_stories: [12, 16, 19, 21]
phase: 2
---

## What to build

The Phase 2 milestone, done by a human. The actuary designs and prices the pilot product on KEN-v1: maize, long rains, phases from the FAO record, triggers reviewed, loadings set, published. Then the blind validation: compare the platform's per-zone rates against the company's withheld internal rates, attribute every gap to a cause using the assumption sheet, and record the verdict. The platform's rates must be produced before the benchmark is looked at.

## Acceptance criteria

- [ ] Product published on KEN-v1 with full assumption sheet
- [ ] Platform rates recorded and timestamped before benchmark consulted
- [ ] Gap analysis per zone: each material difference attributed (data years, loadings, cover design, method)
- [ ] Verdict recorded: engine trusted / issues found (with follow-up issues filed)

## Blocked by

- 007-hitl-kenya-zone-map
- 011-publish-registry
