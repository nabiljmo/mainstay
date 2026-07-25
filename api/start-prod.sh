#!/usr/bin/env sh
# Production entrypoint: one container runs the API and the Celery worker+beat
# together, so they share the weather-cache disk (a hosted disk attaches to a
# single service). Fine for the pilot's load; to scale, split the worker out and
# move the CHIRPS cache to object storage. Locally, docker-compose runs the API,
# worker and beat as separate services instead — this script is prod-only.
set -e

celery -A app.worker.celery_app worker --beat --loglevel=info &
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
