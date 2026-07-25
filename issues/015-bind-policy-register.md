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

- [x] Quote → bind flow creates master policy + schedule entries; individual sale = one-farmer schedule
- [x] PII fields encrypted at rest; access role-scoped
- [x] Premium receipt recording activates the policy; statuses: draft/active/expired/settled
- [x] Register navigable by partner, zone, product, agent
- [x] Tests: two-level integrity (no orphan schedule rows), status transitions, agent scoping

## Blocked by

- 014-quote-api-agent-page

## Notes

- `app/crypto.py`: Fernet (AES) PII encryption keyed from AEZ_PII_KEY (dev
  fallback derives a deterministic non-secret key — set the env var in prod;
  losing it makes PII unrecoverable). Added `cryptography` to deps.
- `app/policies.py`: `master_policies` + `policy_schedule` (FK cascade — no
  orphan rows; master+schedule written in one transaction). Bind turns quotes
  into schedule rows (individual = 1, partner = many, partner name required),
  encrypting name/phone/national_id; gender kept plain for reporting. Receipt
  recording flips draft→active; `set_status` guards transitions
  (active→expired/settled, draft→expired). List is register-navigable by
  partner/zone/product/agent/status; detail decrypts PII for authorised roles.
- Endpoints: POST /policies (agent), GET /policies (scoped + filters),
  GET /policies/{id} (owner/ops/admin, PII), POST /policies/{id}/receipt,
  POST /policies/{id}/status (ops/admin). Agent scoping mirrors quotes:
  agent A cannot read agent B's policy.
- Frontend: agent page gained a bind step (buy → farmer form → policy →
  record payment → active) and a "Policies" register tab for operations/admin
  (filters + drill into the decrypted schedule + status actions).
- `tests/test_policies.py` (10): individual/partner bind, two-level integrity,
  PII ciphertext-at-rest, receipt→active, status transitions, agent scoping,
  register filters. Full suite 107 passing.

Deferred: settlement/payout is issue 016/017 (the 'settled' status is wired but
driven manually here). Cover period / expiry dates are minimal — a season
calendar can refine 'expired' later.
