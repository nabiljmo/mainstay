---
id: 014
title: Quoting — GPS pin to premium in under a second
type: AFK
labels: [needs-triage]
blocked_by: [011, 013]
user_stories: [22, 23, 24, 25]
phase: 3
---

## What to build

The quote path, twice over the same service: a partner REST API (location + crop + season + sum insured → quote) and a deliberately lightweight phone-friendly agent page (GPS pin on a small map, or village/admin-area picker as fallback; crop; sum insured). PostGIS point-in-polygon → zone → published rate → premium. Quote persisted with a reference number, product version link, and plain-language cover summary. A pin with no published product returns "not yet available here" and logs a demand signal.

## Acceptance criteria

- [x] API endpoint returns quote with reference in <1s against a published product
- [x] Agent page works on a low-end Android viewport over throttled connection (page weight budgeted)
- [x] Village-picker fallback produces the same quote as an equivalent pin
- [x] No-product case: friendly message + demand signal recorded with location/crop
- [x] Quote record traces to the exact product version
- [x] Integration tests: known pin in known zone → published rate; boundary pin cases

## Blocked by

- 011-publish-registry
- 013-roles-access

## Notes

Delivered the quote path; auth/agent-scoping deferred to 013 (its concern).

- `app/quotes.py`: `quotes` + `demand_signals` tables; shapely point-in-polygon
  over the published product's frozen zone map → zone → published rate →
  premium (scaled to the requested sum insured); quote persisted with a
  reference, product-version trace, and plain-language cover summary. Pin with
  no product / outside the map → friendly message + demand signal logged with
  location + crop. Verified ~0.15s per quote.
- Endpoints: `POST /quotes`, `GET /quotes/{reference}`, `GET /quote-areas`
  (admin districts + representative points for the village fallback, from
  cached GADM), `GET /demand-signals`, `GET /agent` (the page).
- Agent page: self-contained ~7 KB HTML, no map library — GPS geolocation +
  village-picker fallback + manual product/SI. The picker resolves to a
  district point and takes the same point path as a pin, so the two quote
  identically. Mobile-verified; ~28 KB total first load.
- `tests/test_quotes.py`: pin→zone→rate, product-version trace, outside-map and
  no-product demand signals, village==pin parity, sub-second.

Follow-up when 013 lands: gate the workbench vs. agent page by role, scope
quotes/demand-signals to the creating agent, and require auth on `POST /quotes`.
A small map on the agent page (confirm/adjust the pin) was intentionally left
out to protect the page-weight budget; revisit if agents need it.
