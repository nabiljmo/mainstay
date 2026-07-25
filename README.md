# Mainstay — Weather Index Insurance Platform

**Mainstay** is a web platform that creates agro-ecological zones for any
African country from open CHIRPS rainfall data, and runs the full lifecycle of
weather index insurance products: design → price → publish → quote → bind →
settle. The name is the promise to the farmer: a mainstay when the rains fail.

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

## Deploying online (Render)

The app deploys from this repo and redeploys automatically on every push to
`main`. [render.yaml](render.yaml) is the blueprint; nothing needs PostGIS.

**One-time setup:**

1. Push this repo to GitHub.
2. In Render: **New → Blueprint**, connect the repo. Render reads `render.yaml`
   and creates the database, Redis, the backend (`aez-api`) and the frontend
   (`aez-frontend`). Approve the plans (a card is required for the paid tiers).
3. Two URLs only exist after the first deploy, so wire them by hand, then
   redeploy both services:
   - On **aez-frontend**, set `VITE_API_URL` to the API URL
     (e.g. `https://aez-api.onrender.com`).
   - On **aez-api**, set `AEZ_ALLOWED_ORIGINS` to the frontend URL
     (e.g. `https://aez-frontend.onrender.com`).

That's it. From then on: commit → push → Render rebuilds → the site updates.

**Environment variables** are documented in [.env.example](.env.example). The
one that matters most in production is `AEZ_PII_KEY` (farmer-PII encryption) —
`render.yaml` has Render generate and keep a strong value automatically.

**Topology note:** to keep the pilot simple, the backend runs the API, the
Celery worker and beat in one service sharing one disk for the CHIRPS cache
(see [api/start-prod.sh](api/start-prod.sh)). To scale, split the worker out and
move the cache to object storage.
