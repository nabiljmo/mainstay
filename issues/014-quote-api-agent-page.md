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

- [ ] API endpoint returns quote with reference in <1s against a published product
- [ ] Agent page works on a low-end Android viewport over throttled connection (page weight budgeted)
- [ ] Village-picker fallback produces the same quote as an equivalent pin
- [ ] No-product case: friendly message + demand signal recorded with location/crop
- [ ] Quote record traces to the exact product version
- [ ] Integration tests: known pin in known zone → published rate; boundary pin cases

## Blocked by

- 011-publish-registry
- 013-roles-access
