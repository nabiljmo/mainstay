"""Crop Library — versioned crop phenology records.

Each crop record holds growth stages (name, duration in days, water-stress
sensitivity weight) and per-country planting windows. Records are versioned:
every save writes a new immutable version; a version referenced by a product
can never change underneath it.

Seed data is drawn from FAO crop calendars and FAO-33/56 crop-coefficient
literature; the numbers below are the FAO-typical values for maize and must
be reviewed by an agronomist (the `reviewed` flag) before a product relies on
them. Stage sensitivity weights follow the well-established pattern that
flowering/silking is the stage most vulnerable to water stress.
"""

from __future__ import annotations

import json

from app.db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS crop_versions (
    id          SERIAL PRIMARY KEY,
    crop        TEXT NOT NULL,
    version     INT NOT NULL,
    stages      JSONB NOT NULL,
    seasons     JSONB NOT NULL,
    source      TEXT NOT NULL DEFAULT '',
    reviewed    BOOLEAN NOT NULL DEFAULT FALSE,
    edited_by   TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (crop, version)
);

CREATE TABLE IF NOT EXISTS product_drafts (
    id           TEXT PRIMARY KEY,
    country      TEXT NOT NULL,
    zone_map     TEXT NOT NULL,
    crop         TEXT NOT NULL,
    crop_version INT NOT NULL,
    season       TEXT NOT NULL,
    years        JSONB NOT NULL,
    sum_insured  DOUBLE PRECISION NOT NULL,
    definition   JSONB NOT NULL,
    created_by   TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# FAO-typical maize phenology. Four stages, ~130-day cycle.
# Sensitivity weights sum to 1.0 and drive phase-limit apportioning later.
MAIZE_SEED = {
    "crop": "maize",
    "stages": [
        {"name": "establishment", "days": 20, "sensitivity": 0.15},
        {"name": "vegetative", "days": 35, "sensitivity": 0.20},
        {"name": "flowering", "days": 25, "sensitivity": 0.40},
        {"name": "grain_filling", "days": 40, "sensitivity": 0.25},
    ],
    # Planting windows per country/season. Kenya long rains: sow late March.
    "seasons": [
        {"country": "KEN", "season": "long_rains", "plant_start": "03-15", "plant_end": "04-15"},
        {"country": "KEN", "season": "short_rains", "plant_start": "10-15", "plant_end": "11-15"},
    ],
    "source": "FAO crop calendar; FAO Irrigation & Drainage Paper 33/56 (maize)",
}


def init_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA)


def _row_to_dict(row) -> dict:
    return {
        "crop": row[0],
        "version": row[1],
        "stages": row[2],
        "seasons": row[3],
        "source": row[4],
        "reviewed": row[5],
        "edited_by": row[6],
        "created_at": row[7].isoformat(),
    }


def validate(stages: list[dict], seasons: list[dict]) -> list[str]:
    """Return a list of soft warnings (never hard errors — advisory like the
    data-quality flag). Callers surface these; they do not block a save."""
    warnings: list[str] = []
    total_days = sum(s.get("days", 0) for s in stages)
    if not (60 <= total_days <= 220):
        warnings.append(f"stage durations sum to {total_days} days — unusual for a season")
    total_sens = sum(s.get("sensitivity", 0) for s in stages)
    if abs(total_sens - 1.0) > 1e-6:
        warnings.append(f"sensitivity weights sum to {total_sens:.2f}, not 1.0 (will be normalised)")
    for s in seasons:
        for key in ("country", "season", "plant_start", "plant_end"):
            if not s.get(key):
                warnings.append(f"season entry missing '{key}'")
    return warnings


def latest_versions() -> list[dict]:
    """Most recent version of every crop."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT DISTINCT ON (crop) crop, version, stages, seasons, source,
                      reviewed, edited_by, created_at
               FROM crop_versions ORDER BY crop, version DESC"""
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def versions_of(crop: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT crop, version, stages, seasons, source, reviewed, edited_by, created_at
               FROM crop_versions WHERE crop = %s ORDER BY version DESC""",
            (crop,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_version(crop: str, version: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """SELECT crop, version, stages, seasons, source, reviewed, edited_by, created_at
               FROM crop_versions WHERE crop = %s AND version = %s""",
            (crop, version),
        ).fetchone()
    return _row_to_dict(row) if row else None


def save_new_version(
    crop: str,
    stages: list[dict],
    seasons: list[dict],
    edited_by: str,
    source: str = "",
    reviewed: bool = False,
) -> dict:
    """Append a new immutable version (version = previous max + 1)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM crop_versions WHERE crop = %s", (crop,)
        ).fetchone()
        next_version = row[0] + 1
        conn.execute(
            """INSERT INTO crop_versions
               (crop, version, stages, seasons, source, reviewed, edited_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (crop, next_version, json.dumps(stages), json.dumps(seasons), source, reviewed, edited_by),
        )
    return get_version(crop, next_version)


def seed_if_empty() -> None:
    """Load the FAO maize seed as version 1 if the library is empty."""
    with connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM crop_versions").fetchone()[0]
    if count == 0:
        save_new_version(
            MAIZE_SEED["crop"],
            MAIZE_SEED["stages"],
            MAIZE_SEED["seasons"],
            edited_by="system-seed",
            source=MAIZE_SEED["source"],
            reviewed=False,
        )
