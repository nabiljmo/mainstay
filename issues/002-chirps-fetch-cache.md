---
id: 002
title: Weather Store — fetch and cache CHIRPS for a country
type: AFK
labels: [needs-triage]
blocked_by: [001]
user_stories: [1, 2, 3, 34]
phase: 1
---

## What to build

The Weather Store module behind a stable interface: request "country + year range" via the UI or API, and a background job fetches daily CHIRPS rainfall from the official UCSB/Climate Hazards Center source into a local cache (configurable location), with visible progress. Subsequent requests for cached data return instantly. A pixel's daily series can be queried by coordinate and date range.

## Acceptance criteria

- [x] UI form: pick country (Kenya first) + year range (min 5 recommended, back to 1981); submission starts a background job with live progress
- [x] Data lands in the configurable cache directory; re-running the same request performs zero downloads
- [x] API returns a daily rainfall series for (lon, lat, date range) from the cache
- [x] Dataset name, version, and fetch date recorded for every fetch
- [x] Cache can be pointed at an external drive via configuration
- [x] Tests: cache-hit/cache-miss behaviour, series extraction correctness against a small fixture file

## Blocked by

- 001-walking-skeleton
