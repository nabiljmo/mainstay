---
id: 011
title: Publish — freeze product, rate table, assumption sheet
type: AFK
labels: [needs-triage]
blocked_by: [010]
user_stories: [19, 20, 21]
phase: 2
---

## What to build

The publish flow: a "make this quotable" confirmation, then the product freezes as a read-only version — zones, crop version, phases, triggers, limits, distribution, loadings, rates — with a full audit record. The published rate table keyed (country, crop, season, zone) becomes the quoting source. The assumption sheet — every input that produced the rates, including dataset/version/years/fetch date and both library versions — is rendered and exportable, satisfying the blind-validation requirement.

## Acceptance criteria

- [ ] Publish confirmation → frozen read-only product version; edits require a new version
- [ ] Audit record: who, when, every parameter
- [ ] Rate table queryable by (country, crop, season, zone)
- [ ] Assumption sheet renders and exports (printable/PDF-able) with all assumptions listed
- [ ] Test: published product immutability at the API level

## Blocked by

- 010-pricing
