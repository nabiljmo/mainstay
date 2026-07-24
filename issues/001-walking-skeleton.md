---
id: 001
title: Walking skeleton — app shell runs end-to-end
type: AFK
labels: [needs-triage]
blocked_by: []
user_stories: [34, 35]
phase: 1
---

## What to build

The thinnest possible complete system: Docker Compose defining api (FastAPI), db (PostgreSQL + PostGIS), worker (Celery + Redis broker), and frontend (React + MapLibre shell). One health endpoint that checks the database and one trivial background job that proves the worker round-trip. Pytest wired and passing. Git repository initialised.

## Acceptance criteria

- [x] `docker compose up` brings up all services on a clean machine
- [x] Browser at localhost shows the app shell with a map canvas
- [x] `/health` reports api + db + worker all OK
- [x] A demo background job can be triggered and its progress polled via the API
- [x] `pytest` runs green in CI-able fashion (no manual setup beyond compose)

## Blocked by

None - can start immediately
