# AEZ Creator & Weather Index Insurance Platform

A web platform that creates agro-ecological zones for any African country from
open CHIRPS rainfall data, and runs the full lifecycle of weather index
insurance products: design → price → publish → quote → bind → settle.

- **Specification:** [SPEC.md](SPEC.md) — every agreed design decision.
- **PRD:** [PRD.md](PRD.md) — problem, user stories, modules, testing.
- **Issues:** [issues/](issues/) — the build broken into vertical slices; work
  them in dependency order (each file lists its blockers).
- **Pilot:** Kenya, maize, long rains.

## Running locally (full stack — requires Docker)

```bash
docker compose up
```

API at http://localhost:8000 (health check: `/health`).

## Running the API alone (no Docker)

```bash
cd api
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

## Tests

```bash
cd api && source .venv/bin/activate
pytest
```
