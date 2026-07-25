"""Settlement — the live season watched through the *same* engine that priced.

Pricing runs a product's terms over each historical year via
``products.historical_table`` → ``index_engine.run_year``. Settlement runs the
exact same path over the *live* season year. That is the whole point: what pays
out can never disagree with what was priced, because it is literally the same
code (asserted by test, not convention — see tests/test_settlement.py).

What settlement adds on top is a calendar rule for *trust*:

  - CHIRPS final data lands ~3 weeks after each month ends (SPEC §9). A phase is
    only **settled** (safe to pay on) once its window has closed AND that lag has
    passed for the window's last month. Before that it is **provisional** — shown
    on the dashboard, clearly labelled, but never written as a settlement.
  - A phase whose window has not finished is **upcoming** — no index yet.

Only settled phases are persisted (``persist_settlement``); provisional values
are computed live for the dashboard and never touch the settlements table.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from app.db import connect
from app.index_engine import planting_day_of_year
from app.products import historical_table

# ~3-week lag after month-end before CHIRPS final is trusted. Printed into
# product terms; a product may override via definition["final_lag_days"].
DEFAULT_FINAL_LAG_DAYS = 21

# Per-phase settlement states.
UPCOMING = "upcoming"        # window not finished — no index yet
PROVISIONAL = "provisional"  # window finished, final data not yet confirmed
SETTLED = "settled"          # CHIRPS final confirmed — safe to pay on


SCHEMA = """
CREATE TABLE IF NOT EXISTS settlements (
    id            SERIAL PRIMARY KEY,
    product_id    TEXT NOT NULL REFERENCES published_products(id),
    season_year   INT NOT NULL,
    zone          INT NOT NULL,
    phase         TEXT NOT NULL,
    cover_type    TEXT NOT NULL,
    index_value   DOUBLE PRECISION NOT NULL,
    payout        DOUBLE PRECISION NOT NULL,
    limit_amount  DOUBLE PRECISION NOT NULL,
    window_start  DATE NOT NULL,
    window_end    DATE NOT NULL,
    final_ready   DATE NOT NULL,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One settled value per product/season/zone/phase. Settled is final: the
    -- first final computation wins and is never silently overwritten (ON
    -- CONFLICT DO NOTHING), the same immutability the publish flow enforces.
    UNIQUE (product_id, season_year, zone, phase)
);
CREATE INDEX IF NOT EXISTS idx_settlements_product
    ON settlements (product_id, season_year);
"""


def init_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA)


# ----- the calendar rule (Option 1: settle on the ~3-week final lag) -----

def _month_end(d: date) -> date:
    last = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last)


def final_ready_date(window_end: date, lag_days: int = DEFAULT_FINAL_LAG_DAYS) -> date:
    """The date a phase's final CHIRPS is trusted: the lag after the month-end of
    the window's last day (final lands per-month, so the last day's month gates)."""
    return _month_end(window_end) + timedelta(days=lag_days)


def phase_window_dates(
    plant_start: str, season_year: int, start_offset: int, end_offset: int
) -> tuple[date, date]:
    """Calendar dates for a phase window, matching run_year's day-offset slice
    exactly: day N of the series is Jan 1 + N days. end_offset is exclusive, so
    the last covered day is end_offset - 1."""
    origin = date(season_year, 1, 1)
    plant_idx = planting_day_of_year(plant_start, season_year)
    start_day = origin + timedelta(days=plant_idx + start_offset)
    last_day = origin + timedelta(days=plant_idx + end_offset - 1)
    return start_day, last_day


def phase_status(
    window_end: date, today: date, lag_days: int = DEFAULT_FINAL_LAG_DAYS
) -> str:
    """Classify a phase by where today sits relative to its window and the lag.

    The window is complete once today is past its last day (CHIRPS publishes a
    day only after it has elapsed), and settled once the final lag has passed."""
    if today <= window_end:
        return UPCOMING
    if today >= final_ready_date(window_end, lag_days):
        return SETTLED
    return PROVISIONAL


# ----- per-zone settlement, live, over the SAME pricing path -----

def settle_zone(
    store,
    product: dict,
    zone_geojson: dict,
    zone_id: int,
    season_year: int,
    *,
    today: date | None = None,
) -> list[dict]:
    """Index/payout + settlement status for every phase of one zone this season.

    Uses products.historical_table (the exact pricing call path) over the single
    live season year. Upcoming phases carry no index/payout (their window is not
    complete, so the slice would be partial and misleading)."""
    today = today or date.today()
    definition = product["definition"]
    country = definition["country"]
    plant_start = definition["plant_start"]
    lag_days = int(definition.get("final_lag_days", DEFAULT_FINAL_LAG_DAYS))
    zdef = definition["zones"].get(str(zone_id)) or definition["zones"].get(zone_id)
    if not zdef:
        return []
    phases_def = zdef["phases"]

    # No CHIRPS for the season year yet → every phase is upcoming, no numbers.
    if season_year not in store.cached_years(country):
        return [
            _phase_row(p, plant_start, season_year, today, lag_days, computed=None)
            for p in phases_def
        ]

    # THE SAME PATH pricing uses: historical_table → run_year, one year.
    table = historical_table(
        store, country, [season_year], zone_geojson, zone_id, phases_def, plant_start
    )
    computed_by_phase: dict[str, dict] = {}
    if table:
        for pr in table[0]["phases"]:
            computed_by_phase[pr["phase"]] = pr

    return [
        _phase_row(
            p, plant_start, season_year, today, lag_days,
            computed=computed_by_phase.get(p["name"]),
        )
        for p in phases_def
    ]


def _phase_row(pdef, plant_start, season_year, today, lag_days, *, computed) -> dict:
    start_day, end_day = phase_window_dates(
        plant_start, season_year, pdef["start_offset"], pdef["end_offset"]
    )
    status = phase_status(end_day, today, lag_days)
    row = {
        "phase": pdef["name"],
        "cover_type": pdef["cover_type"],
        "limit": pdef["limit"],
        "window_start": start_day.isoformat(),
        "window_end": end_day.isoformat(),
        "final_ready": final_ready_date(end_day, lag_days).isoformat(),
        "status": status,
        "index": None,
        "payout": None,
    }
    # Only surface a number once the window is complete (provisional/settled).
    # Upcoming phases would compute over a partial slice — never show that.
    if status != UPCOMING and computed is not None:
        row["index"] = computed["index"]
        row["payout"] = computed["payout"]
    return row


# ----- persistence: ONLY settled (final) values are ever written -----

def persist_settlement(product_id: str, season_year: int, zone_id: int,
                       phase_rows: list[dict]) -> int:
    """Write the settled phases for a zone. Provisional/upcoming rows are skipped
    here — that is the guarantee that a provisional value never becomes a
    settlement. Idempotent: a settled value, once written, is never overwritten."""
    final_rows = [p for p in phase_rows if p["status"] == SETTLED]
    if not final_rows:
        return 0
    written = 0
    with connect() as conn:
        with conn.transaction():
            for p in final_rows:
                cur = conn.execute(
                    """INSERT INTO settlements
                       (product_id, season_year, zone, phase, cover_type,
                        index_value, payout, limit_amount,
                        window_start, window_end, final_ready)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (product_id, season_year, zone, phase)
                       DO NOTHING""",
                    (product_id, season_year, zone_id, p["phase"], p["cover_type"],
                     p["index"], p["payout"], p["limit"],
                     p["window_start"], p["window_end"], p["final_ready"]),
                )
                written += cur.rowcount
    return written


def persisted_settlements(product_id: str, season_year: int) -> dict:
    """Settled rows already written, keyed (zone, phase) → the official values."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT zone, phase, cover_type, index_value, payout, limit_amount,
                      window_start, window_end, final_ready, computed_at
               FROM settlements WHERE product_id = %s AND season_year = %s""",
            (product_id, season_year),
        ).fetchall()
    return {
        (r[0], r[1]): {
            "zone": r[0], "phase": r[1], "cover_type": r[2], "index": r[3],
            "payout": r[4], "limit": r[5],
            "window_start": r[6].isoformat(), "window_end": r[7].isoformat(),
            "final_ready": r[8].isoformat(), "computed_at": r[9].isoformat(),
        }
        for r in rows
    }


# ----- which season, which zones, which map -----

def season_year_for(product_id: str, today: date | None = None) -> int:
    """The live season's calendar year for a product.

    Policies are bound before the season's planting, within the same calendar
    year as the planting for the pilot's single-year seasons (Kenya long/short
    rains). So the year of the latest bound policy is the season year. Falls back
    to today's year when nothing is bound yet. Swappable when seasons that span a
    year boundary arrive."""
    with connect() as conn:
        row = conn.execute(
            "SELECT MAX(created_at) FROM master_policies WHERE product_id = %s",
            (product_id,),
        ).fetchone()
    if row and row[0]:
        return row[0].year
    return (today or date.today()).year


def zone_policy_counts(product_id: str) -> dict[int, int]:
    """Farmers bound per zone for a product (schedule rows under its masters)."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.zone, COUNT(*)
               FROM policy_schedule s
               JOIN master_policies m ON m.id = s.master_policy_id
               WHERE m.product_id = %s
               GROUP BY s.zone""",
            (product_id,),
        ).fetchall()
    return {int(r[0]): int(r[1]) for r in rows}


def _zone_geojson(zone_map_name: str) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT geojson FROM zone_map_versions WHERE name = %s", (zone_map_name,)
        ).fetchone()
    if not row:
        raise ValueError(f"zone map {zone_map_name} not found")
    return row[0]


# ----- the dashboard: the season watched live, provisional vs settled -----

def season_view(
    store,
    product_id: str,
    *,
    season_year: int | None = None,
    today: date | None = None,
) -> dict:
    """Phase-by-phase progress for every zone of a product this season.

    Merges the official persisted settlements (settled phases) with a live
    computation for the rest. A phase is marked ``persisted`` only when a
    settlement row exists — so provisional and settled are never confused."""
    from app.publish import get_published

    today = today or date.today()
    product = get_published(product_id)
    if not product:
        raise ValueError(f"no published product {product_id}")
    season_year = season_year or season_year_for(product_id, today)

    definition = product["definition"]
    zone_geojson = _zone_geojson(product["zone_map"])
    counts = zone_policy_counts(product_id)
    persisted = persisted_settlements(product_id, season_year)

    zone_ids = sorted(int(z) for z in definition["zones"])
    zones = []
    for zid in zone_ids:
        live = settle_zone(store, product, zone_geojson, zid, season_year, today=today)
        phases = []
        for p in live:
            official = persisted.get((zid, p["phase"]))
            if official is not None:
                # Show the frozen settled number, not a re-computation.
                phases.append({**p, "index": official["index"],
                               "payout": official["payout"],
                               "status": SETTLED, "persisted": True})
            else:
                phases.append({**p, "persisted": False})
        settled_payout = sum(
            p["payout"] for p in phases if p["status"] == SETTLED and p["payout"]
        )
        provisional_payout = sum(
            p["payout"] for p in phases if p["status"] == PROVISIONAL and p["payout"]
        )
        zones.append({
            "zone": zid,
            "policies": counts.get(zid, 0),
            "phases": phases,
            "settled_payout": round(settled_payout, 2),
            "provisional_payout": round(provisional_payout, 2),
        })

    return {
        "product_id": product_id,
        "country": product["country"],
        "crop": product["crop"],
        "season": product["season"],
        "season_year": season_year,
        "sum_insured": product["sum_insured"],
        "as_of": today.isoformat(),
        "final_lag_days": int(definition.get("final_lag_days", DEFAULT_FINAL_LAG_DAYS)),
        "zones": zones,
    }


# ----- the scheduled sweep: find newly-final phases and settle them -----

def run_settlement_sweep(
    store=None,
    *,
    product_id: str | None = None,
    season_year: int | None = None,
    today: date | None = None,
    refresh: bool = True,
) -> dict:
    """Compute and persist every phase that has newly gone final.

    Run on a schedule (Celery beat) with no arguments to sweep all products, or
    on demand for one. Refreshes the live season's CHIRPS first so newly
    published final days are picked up — that is the data-availability check.
    Only settled phases are written; provisional never persists."""
    from pathlib import Path

    from app.config import settings
    from app.countries import COUNTRIES
    from app.publish import get_published, list_published
    from app.weather import WeatherStore

    if store is None:
        store = WeatherStore(cache_dir=Path(settings.weather_cache_dir))
    today = today or date.today()

    product_ids = [product_id] if product_id else [p["id"] for p in list_published()]
    summary = {"products": 0, "zones": 0, "phases_settled": 0, "details": []}

    for pid in product_ids:
        product = get_published(pid)
        if not product:
            continue
        country = product["country"]
        syear = season_year or season_year_for(pid, today)

        if refresh and country in COUNTRIES:
            # The data-availability check: pull any newly published days for the
            # live season before recomputing. A completed past year is a no-op.
            try:
                store.refresh_year(country, syear, tuple(COUNTRIES[country]["bbox"]))
            except Exception:
                pass  # a fetch hiccup must not abort the whole sweep

        try:
            zone_geojson = _zone_geojson(product["zone_map"])
        except ValueError:
            continue

        settled_here = 0
        zones_here = 0
        for zid in sorted(int(z) for z in product["definition"]["zones"]):
            live = settle_zone(store, product, zone_geojson, zid, syear, today=today)
            written = persist_settlement(pid, syear, zid, live)
            settled_here += written
            zones_here += 1
        summary["products"] += 1
        summary["zones"] += zones_here
        summary["phases_settled"] += settled_here
        summary["details"].append(
            {"product_id": pid, "season_year": syear, "phases_settled": settled_here}
        )

    return summary
