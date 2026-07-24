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

- [x] Publish confirmation → frozen read-only product version; edits require a new version
- [x] Audit record: who, when, every parameter
- [x] Rate table queryable by (country, crop, season, zone)
- [x] Assumption sheet renders and exports (printable/PDF-able) with all assumptions listed
- [x] Test: published product immutability at the API level

## Notes

Delivered:
- Backend `app/publish.py`: `published_products` + `published_rates` tables,
  immutable versioning (re-publish mints v2, never overwrites), rate query
  returning the latest version, printable assumption sheet.
- Endpoints: `POST /products/drafts/{id}/publish`, `GET /products/published`,
  `GET /products/published/{id}`, `GET /products/published/{id}/assumption-sheet`,
  `GET /rates?country=&crop=&season=&zone=`.
- Shared `app/economics.py` so a published rate is exactly the number priced on
  screen.
- UI: "Publish product" in the sticky bar → confirm → green banner with rate
  table, assumption-sheet link, and frozen-product link. Per-zone edits are
  captured and frozen.
- `tests/test_publish.py`: 6 tests (freeze, versioning, API immutability, rate
  query/latest, golden rate match, assumption sheet).

Fixes found along the way:
- `propose_product` could emit percent triggers that, once rounded to 1 dp,
  violated strike/exit ordering → some zones couldn't be priced. Guarded.
- `expected_loss` now falls back to burning cost when a severity fit diverges
  on near-degenerate payouts, instead of raising.

Unblocks 012 (Kenya blind validation).

## Blocked by

- 010-pricing
