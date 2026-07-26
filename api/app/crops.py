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


# ---------------------------------------------------------------------------
# Comprehensive FAO-typical crop library
#
# Every crop keeps the four canonical stage names (establishment / vegetative /
# flowering / grain_filling) so the cover-type defaults stay sensible
# (establishment + flowering default to dry-spell, the rest to deficit) and the
# workbench UI is uniform. Only the stage *durations* and *sensitivity weights*
# change per crop — durations from FAO-56 growth-stage lengths, weights from the
# FAO-33 yield-response pattern (flowering/reproductive is the most water-stress
# -sensitive stage). Sensitivity weights sum to 1.0.
#
# Planting windows are FAO GIEWS Country-Crop-Calendar *regional typicals*: the
# broad sowing month per agro-climatic zone, not a district-precise date. Every
# record is seeded reviewed=False on purpose — an agronomist must confirm the
# stage numbers and the exact local sowing window before a product relies on it.
#
# The season enum is only long_rains / short_rains, so each country's main
# season maps to long_rains and (in the bimodal East African belt) the second
# season maps to short_rains.
# ---------------------------------------------------------------------------

_LIBRARY_SOURCE = (
    "FAO GIEWS Country Crop Calendar; FAO Irrigation & Drainage Paper 33/56 "
    "— FAO-typical values, pending agronomist review"
)


def _stages(est, veg, flr, grn, sens):
    """Four canonical stages from (days...) and a (sensitivity...) tuple."""
    names = ("establishment", "vegetative", "flowering", "grain_filling")
    return [
        {"name": n, "days": d, "sensitivity": s}
        for n, d, s in zip(names, (est, veg, flr, grn), sens)
    ]


def _win(country, season, start, end):
    return {"country": country, "season": season, "plant_start": start, "plant_end": end}


# Regional planting-window builders (sowing month range, MM-DD). A country's
# main rainy season is long_rains; the bimodal East-African second season is
# short_rains.
def _east_lr(cs):   return [_win(c, "long_rains", "03-01", "04-15") for c in cs]
def _east_sr(cs):   return [_win(c, "short_rains", "10-01", "11-15") for c in cs]
def _eth_lr():      return [_win("ETH", "long_rains", "06-01", "07-15")]  # Meher
def _southern(cs):  return [_win(c, "long_rains", "11-15", "12-31") for c in cs]
def _sahel(cs):     return [_win(c, "long_rains", "06-15", "07-15") for c in cs]
def _west(cs):      return [_win(c, "long_rains", "05-15", "06-30") for c in cs]


CROP_LIBRARY = [
    {
        "crop": "sorghum",
        "stages": _stages(20, 40, 25, 35, (0.15, 0.20, 0.35, 0.30)),
        "seasons": (
            _east_lr(["KEN", "TZA", "UGA"]) + _eth_lr()
            + _sahel(["SEN", "MLI", "BFA", "NER"]) + _west(["NGA", "GHA"])
            + _southern(["ZMB", "MWI", "ZWE", "MOZ"])
        ),
        "source": _LIBRARY_SOURCE,
    },
    {
        "crop": "pearl_millet",
        "stages": _stages(15, 30, 20, 25, (0.15, 0.20, 0.35, 0.30)),
        "seasons": (
            _sahel(["SEN", "MLI", "BFA", "NER"]) + _west(["NGA"])
            + _east_lr(["KEN", "TZA"]) + _eth_lr()
        ),
        "source": _LIBRARY_SOURCE,
    },
    {
        "crop": "finger_millet",
        "stages": _stages(20, 35, 25, 30, (0.15, 0.20, 0.35, 0.30)),
        "seasons": (
            _east_lr(["UGA", "KEN", "TZA"]) + _eth_lr()
            + _southern(["ZMB", "MWI", "ZWE"])
        ),
        "source": _LIBRARY_SOURCE,
    },
    {
        "crop": "rice",
        "stages": _stages(25, 45, 30, 30, (0.15, 0.20, 0.35, 0.30)),
        "seasons": (
            _west(["NGA", "GHA"]) + _sahel(["SEN", "MLI"])
            + _east_lr(["TZA", "UGA"]) + _southern(["MOZ", "MWI"])
        ),
        "source": _LIBRARY_SOURCE,
    },
    {
        "crop": "groundnut",
        "stages": _stages(20, 40, 35, 25, (0.15, 0.25, 0.35, 0.25)),
        "seasons": (
            _sahel(["SEN", "MLI", "BFA", "NER"]) + _west(["NGA", "GHA"])
            + _east_lr(["KEN", "TZA", "UGA"]) + _eth_lr()
            + _southern(["ZMB", "MWI", "ZWE", "MOZ"])
        ),
        "source": _LIBRARY_SOURCE,
    },
    {
        "crop": "cowpea",
        "stages": _stages(15, 30, 20, 15, (0.15, 0.25, 0.35, 0.25)),
        "seasons": (
            _west(["NGA", "GHA"]) + _sahel(["SEN", "MLI", "BFA", "NER"])
            + _east_lr(["KEN", "TZA", "UGA"]) + _east_sr(["KEN", "TZA", "UGA"])
            + _southern(["MOZ", "MWI"])
        ),
        "source": _LIBRARY_SOURCE,
    },
    {
        "crop": "common_bean",
        "stages": _stages(15, 30, 25, 25, (0.15, 0.20, 0.40, 0.25)),
        "seasons": (
            _east_lr(["KEN", "UGA", "RWA", "TZA"]) + _east_sr(["KEN", "UGA", "RWA", "TZA"])
            + _eth_lr() + _southern(["ZMB", "MWI", "ZWE", "MOZ"])
        ),
        "source": _LIBRARY_SOURCE,
    },
    {
        "crop": "soybean",
        "stages": _stages(20, 40, 35, 25, (0.15, 0.20, 0.40, 0.25)),
        "seasons": (
            _southern(["ZMB", "MWI", "ZWE", "MOZ"]) + _west(["NGA", "GHA"])
            + _east_lr(["UGA"]) + _eth_lr()
        ),
        "source": _LIBRARY_SOURCE,
    },
    {
        "crop": "sunflower",
        "stages": _stages(20, 35, 30, 25, (0.15, 0.20, 0.40, 0.25)),
        "seasons": (
            _east_lr(["TZA", "UGA", "KEN"]) + _southern(["ZMB", "ZWE", "MWI"])
        ),
        "source": _LIBRARY_SOURCE,
    },
    {
        "crop": "wheat",
        "stages": _stages(20, 40, 25, 35, (0.15, 0.20, 0.35, 0.30)),
        "seasons": _eth_lr() + _east_lr(["KEN"]),
        "source": _LIBRARY_SOURCE,
    },
    {
        "crop": "cotton",
        "stages": _stages(25, 45, 50, 40, (0.10, 0.25, 0.40, 0.25)),
        "seasons": (
            _east_lr(["TZA"]) + _sahel(["MLI", "BFA", "SEN"]) + _west(["NGA"])
            + _southern(["ZMB", "ZWE", "MOZ"]) + _eth_lr()
        ),
        "source": _LIBRARY_SOURCE,
    },
    {
        "crop": "sesame",
        "stages": _stages(20, 35, 25, 20, (0.15, 0.25, 0.35, 0.25)),
        "seasons": (
            _eth_lr() + _east_lr(["TZA", "UGA"]) + _west(["NGA"]) + _southern(["MOZ"])
        ),
        "source": _LIBRARY_SOURCE,
    },
]


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


def seed_library() -> None:
    """Idempotently add every crop in CROP_LIBRARY that isn't already present.

    Additive and safe on a live database: a crop that already has *any* version
    (including agronomist edits) is left completely untouched — we only ever
    create version 1 of a crop the library has and the database doesn't. This is
    what lets a deploy backfill the new crops without clobbering existing ones.
    """
    with connect() as conn:
        existing = {
            r[0] for r in conn.execute("SELECT DISTINCT crop FROM crop_versions").fetchall()
        }
    for entry in CROP_LIBRARY:
        if entry["crop"] in existing:
            continue
        save_new_version(
            entry["crop"], entry["stages"], entry["seasons"],
            edited_by="system-seed", source=entry["source"], reviewed=False,
        )
