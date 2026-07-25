"""Settlement / season dashboard tests (issue 016).

Two layers:

  * Pure, DB-free tests of the calendar rule and — critically — proof that
    settlement runs through the *same* index-engine call path as pricing
    (``app.products.run_year``), asserted with a spy, not by convention.
  * An end-to-end DB test: a synthetic season with known rainfall produces the
    exact expected phase payouts on the dashboard, and a provisional phase never
    persists as a settlement. Skips when no PostgreSQL is reachable.
"""

import json
import uuid
from datetime import date

import numpy as np
import pytest

from app.config import settings
from app.products import historical_table
from app.settlement import (
    PROVISIONAL,
    SETTLED,
    UPCOMING,
    final_ready_date,
    persist_settlement,
    persisted_settlements,
    phase_status,
    phase_window_dates,
    run_settlement_sweep,
    season_view,
    settle_zone,
)
from app.weather import WeatherStore

DB_URL = "postgresql://aez:aez@localhost:5432/aez"

GRID = {"x0": 34.0, "y0": 2.0, "dx": 0.5, "dy": -0.5, "nx": 6, "ny": 6}
BBOX = (34.0, -1.0, 37.0, 2.0)
SEASON_YEAR = 2023

ZONE_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"zone": 1},
         "geometry": {"type": "Polygon", "coordinates": [
             [[33.9, -1.1], [35.4, -1.1], [35.4, 2.1], [33.9, 2.1], [33.9, -1.1]]]}},
        {"type": "Feature", "properties": {"zone": 2},
         "geometry": {"type": "Polygon", "coordinates": [
             [[35.6, -1.1], [37.1, -1.1], [37.1, 2.1], [35.6, 2.1], [35.6, -1.1]]]}},
    ],
}

# One deficit phase, absolute triggers, so the payouts are hand-computable:
# index = total phase rainfall; payout = (strike - index)/(strike - exit) * limit.
PHASE = {
    "name": "grain_filling", "cover_type": "deficit",
    "start_offset": 0, "end_offset": 30,
    "strike": 100.0, "exit": 0.0, "limit": 10000.0,
    "trigger_mode": "absolute",
}
DEFINITION = {
    "country": "TST", "sum_insured": 10000.0, "plant_start": "03-01",
    "final_lag_days": 21,
    "zones": {"1": {"phases": [PHASE]}, "2": {"phases": [PHASE]}},
}


class Fetcher:
    """West (lon<35.5, zone 1): 8mm/day → 240mm over 30 days (wet, no payout).
    East (zone 2): 1mm/day → 30mm over 30 days (dry, pays 70%)."""

    def __call__(self, day: date, bbox: tuple):
        lon_axis = GRID["x0"] + np.arange(GRID["nx"]) * GRID["dx"]
        arr = np.ones((GRID["ny"], GRID["nx"]), dtype="float32")
        arr[:, lon_axis < 35.5] = 8.0
        return arr, dict(GRID)


@pytest.fixture
def store(tmp_path):
    s = WeatherStore(cache_dir=tmp_path, fetch_day=Fetcher())
    s.ensure_year("TST", SEASON_YEAR, BBOX)
    return s


def _product():
    return {"definition": DEFINITION, "country": "TST", "crop": "maize",
            "season": "long_rains", "sum_insured": 10000.0, "zone_map": "tst-map"}


# ----------------------------------------------------------------- calendar rule

def test_phase_window_dates_match_run_year_offsets():
    start, end = phase_window_dates("03-01", 2023, 0, 30)
    assert start == date(2023, 3, 1)
    assert end == date(2023, 3, 30)          # end_offset is exclusive → last day 29 after start


def test_final_ready_is_lag_after_month_end():
    # Window ends 2023-03-30 → March month-end is 03-31 → +21 days = 04-21.
    assert final_ready_date(date(2023, 3, 30), 21) == date(2023, 4, 21)


def test_status_progression_upcoming_provisional_settled():
    end = date(2023, 3, 30)
    assert phase_status(end, date(2023, 3, 15), 21) == UPCOMING      # window still open
    assert phase_status(end, date(2023, 4, 5), 21) == PROVISIONAL    # closed, pre-lag
    assert phase_status(end, date(2023, 5, 1), 21) == SETTLED        # past the lag


# --------------------------------------------------- same engine path as pricing

def test_settlement_uses_the_same_run_year_as_pricing(store, monkeypatch):
    """The core invariant: settlement flows through app.products.run_year — the
    exact function pricing uses — not a parallel re-implementation."""
    import app.products as products_mod

    calls = []
    original = products_mod.run_year

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(products_mod, "run_year", spy)
    settle_zone(store, _product(), ZONE_GEOJSON, 2, SEASON_YEAR, today=date(2023, 5, 1))
    assert calls, "settlement must compute through products.run_year"


def test_settlement_numbers_equal_a_direct_pricing_computation(store):
    """Belt-and-braces: the settled index/payout equal historical_table run
    directly on the same terms — pricing and settlement cannot disagree."""
    direct = historical_table(
        store, "TST", [SEASON_YEAR], ZONE_GEOJSON, 2, [PHASE], "03-01"
    )[0]["phases"][0]

    phases = settle_zone(store, _product(), ZONE_GEOJSON, 2, SEASON_YEAR,
                         today=date(2023, 5, 1))
    settled = phases[0]
    assert settled["status"] == SETTLED
    assert settled["index"] == pytest.approx(direct["index"])
    assert settled["payout"] == pytest.approx(direct["payout"])
    # Zone 2 is dry (30mm vs 100 strike) → 70% of the 10 000 limit.
    assert settled["payout"] == pytest.approx(7000.0)


def test_upcoming_phase_shows_no_number(store):
    phases = settle_zone(store, _product(), ZONE_GEOJSON, 2, SEASON_YEAR,
                         today=date(2023, 3, 10))
    assert phases[0]["status"] == UPCOMING
    assert phases[0]["index"] is None and phases[0]["payout"] is None


def test_wet_zone_pays_nothing(store):
    phases = settle_zone(store, _product(), ZONE_GEOJSON, 1, SEASON_YEAR,
                         today=date(2023, 5, 1))
    assert phases[0]["status"] == SETTLED
    assert phases[0]["payout"] == pytest.approx(0.0)   # 240mm >> 100 strike


# --------------------------------------------------------------- end-to-end (DB)

@pytest.fixture
def env(monkeypatch, tmp_path, login):
    """Live DB (or skip) + synthetic CHIRPS + a published product, zone map, and
    two bound farmers. Yields (client, product_id). Sweeps TST rows in teardown."""
    monkeypatch.setattr(settings, "database_url", DB_URL)
    monkeypatch.setattr(settings, "weather_cache_dir", str(tmp_path))

    try:
        from app import auth, policies, publish, settlement
        from app.db import connect, init_schema

        init_schema()
        publish.init_schema()
        policies.init_schema()
        settlement.init_schema()
        auth.init_schema()
    except Exception:
        pytest.skip("PostgreSQL not reachable — run `docker compose up db`")

    store = WeatherStore(cache_dir=tmp_path, fetch_day=Fetcher())
    store.ensure_year("TST", SEASON_YEAR, BBOX)

    zm_name = f"pytest-tst-{uuid.uuid4().hex[:8]}"
    product_id = f"TST-maize-long_rains-{uuid.uuid4().hex[:6]}"
    master_id = f"MP-TST-{uuid.uuid4().hex[:8]}"

    with connect() as conn:
        conn.execute(
            """INSERT INTO zone_map_versions
               (name, country, run_id, params, homogeneity, geojson, approved_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (zm_name, "TST", "test-run", json.dumps({}), json.dumps({}),
             json.dumps(ZONE_GEOJSON), "pytest"))
        conn.execute(
            """INSERT INTO published_products
               (id, draft_id, country, crop, crop_version, season, zone_map,
                version, sum_insured, years, distribution, loadings,
                definition, assumptions, audit, published_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (product_id, "draft", "TST", "maize", 1, "long_rains", zm_name, 1,
             10000.0, json.dumps([SEASON_YEAR]), "gamma", json.dumps([]),
             json.dumps(DEFINITION), json.dumps({}), json.dumps({}), "pytest"))
        conn.execute(
            """INSERT INTO master_policies
               (id, sale_type, partner_name, product_id, country, crop, season,
                status, total_sum_insured, total_premium, created_by)
               VALUES (%s,'partner','ACME',%s,'TST','maize','long_rains',
                       'active',%s,%s,'test')""",
            (master_id, product_id, 20000.0, 2000.0))
        for zone in (1, 2):
            conn.execute(
                """INSERT INTO policy_schedule
                   (master_policy_id, quote_reference, zone, sum_insured,
                    premium_rate, premium, name_enc, phone_enc)
                   VALUES (%s,NULL,%s,%s,%s,%s,%s,%s)""",
                (master_id, zone, 10000.0, 10.0, 1000.0, "enc", "enc"))

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    login(client, "operations")
    yield client, product_id

    with connect() as conn:
        conn.execute("DELETE FROM settlements WHERE product_id = %s", (product_id,))
        conn.execute("DELETE FROM policy_schedule WHERE master_policy_id = %s", (master_id,))
        conn.execute("DELETE FROM master_policies WHERE id = %s", (master_id,))
        conn.execute("DELETE FROM published_products WHERE id = %s", (product_id,))
        conn.execute("DELETE FROM zone_map_versions WHERE name = %s", (zm_name,))
        conn.execute("DELETE FROM users WHERE created_by = 'test'")


def test_dashboard_shows_known_payouts_per_zone(env):
    client, product_id = env
    r = client.get("/settlement/season",
                   params={"product_id": product_id, "season_year": SEASON_YEAR})
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["season_year"] == SEASON_YEAR
    zones = {z["zone"]: z for z in view["zones"]}
    # Both zones carry a bound farmer; the dry east zone pays 70% of its limit.
    assert zones[1]["policies"] == 1 and zones[2]["policies"] == 1
    z2_phase = zones[2]["phases"][0]
    assert z2_phase["payout"] == pytest.approx(7000.0)
    assert zones[1]["phases"][0]["payout"] == pytest.approx(0.0)


def test_run_persists_settled_and_dashboard_marks_it(env):
    client, product_id = env
    run = client.post("/settlement/run",
                      json={"product_id": product_id, "season_year": SEASON_YEAR})
    assert run.status_code == 200, run.text
    # Two zones, one final phase each → two settled rows.
    assert run.json()["phases_settled"] == 2

    view = client.get("/settlement/season",
                      params={"product_id": product_id, "season_year": SEASON_YEAR}).json()
    z2 = next(z for z in view["zones"] if z["zone"] == 2)
    assert z2["phases"][0]["status"] == SETTLED
    assert z2["phases"][0]["persisted"] is True
    assert z2["settled_payout"] == pytest.approx(7000.0)


def test_provisional_never_persists_as_settlement(env):
    client, product_id = env
    store = WeatherStore(cache_dir=settings.weather_cache_dir, fetch_day=Fetcher())

    # As of 2023-04-05 the phase window has closed but the final lag has not
    # passed — it is provisional and must not be written.
    run_settlement_sweep(store, product_id=product_id, season_year=SEASON_YEAR,
                         today=date(2023, 4, 5))
    assert persisted_settlements(product_id, SEASON_YEAR) == {}

    # Past the lag it settles.
    run_settlement_sweep(store, product_id=product_id, season_year=SEASON_YEAR,
                         today=date(2023, 5, 1))
    assert len(persisted_settlements(product_id, SEASON_YEAR)) == 2


def test_settled_value_is_immutable_once_written(env):
    client, product_id = env
    store = WeatherStore(cache_dir=settings.weather_cache_dir, fetch_day=Fetcher())
    first = run_settlement_sweep(store, product_id=product_id, season_year=SEASON_YEAR,
                                 today=date(2023, 5, 1))
    second = run_settlement_sweep(store, product_id=product_id, season_year=SEASON_YEAR,
                                  today=date(2023, 5, 1))
    assert first["phases_settled"] == 2
    assert second["phases_settled"] == 0   # ON CONFLICT DO NOTHING — never re-written
