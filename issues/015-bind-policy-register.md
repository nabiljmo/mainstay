---
id: 015
title: Binding — two-level policy register
type: AFK
labels: [needs-triage]
blocked_by: [014]
user_stories: [26, 27, 28]
phase: 4
---

## What to build

Convert quotes into policies: a master policy (partner or individual sale) with a schedule of farmers beneath it. Farmer record holds minimal PII — name, phone, gender, optional national ID — encrypted at rest. Premium receipt is recorded (reference + date), flipping policy status to active. Money is never moved. Register screens: master policy list, schedule view, statuses, per-agent scoping.

## Acceptance criteria

- [ ] Quote → bind flow creates master policy + schedule entries; individual sale = one-farmer schedule
- [ ] PII fields encrypted at rest; access role-scoped
- [ ] Premium receipt recording activates the policy; statuses: draft/active/expired/settled
- [ ] Register navigable by partner, zone, product, agent
- [ ] Tests: two-level integrity (no orphan schedule rows), status transitions, agent scoping

## Blocked by

- 014-quote-api-agent-page
