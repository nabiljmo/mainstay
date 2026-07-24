"""Minimal database layer: plain psycopg, schema bootstrapped on startup.

Approved zone-map versions are the first durable records — the artefacts
products will reference. Draft zoning runs stay as files in the cache;
approval is the moment data enters the database and becomes immutable.
"""

import psycopg

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS zone_map_versions (
    id          SERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    country     TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    params      JSONB NOT NULL,
    homogeneity JSONB NOT NULL,
    geojson     JSONB NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def connect() -> psycopg.Connection:
    if not settings.database_url:
        raise RuntimeError("AEZ_DATABASE_URL is not configured")
    return psycopg.connect(settings.database_url)


def init_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA)
