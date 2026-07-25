"""Quoting tests (issue 014). Needs the docker-compose PostgreSQL (skips
otherwise). Publishes a synthetic product on country "TST", then quotes against
it via the API. Weather + admin boundaries are synthetic — no network."""

import json
import uuid
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from app.config import settings
from app.pricing import DEFAULT_LOADINGS
from app.products import propose_product
from app.weather import WeatherStore

DB_URL = "postgresql://aez:aez@localhost:5432/aez"

GRID = {"x0": 34.0, "y0": 2.0, "dx": 0.5, "dy": -0.5, "nx": 6, "ny": 6}
BBOX = (34.0, -1.0, 37.0, 2.0)
STAGES = [
    {"name": "establishment", "days": 20, "sensitivity": 0.15},
    {"name": "vegetative", "days": 35, "sensitivity": 0.20},
    {"name": "flowering", "days": 25, "sensitivity": 0.40},
    {"name": "grain_filling", "days": 40, "sensitivity": 0.25},
]
# West half (lon < 35.5) = zone 1, east half = zone 2.
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
YEARS = [2021, 2022, 2023]
PIN_Z1 = {"lat": 0.5, "lon": 34.5}    # inside zone 1
PIN_Z2 = {"lat": 0.5, "lon": 36.5}    # inside zone 2
PIN_OUT = {"lat": 10.0, "lon": 50.0}  # outside the map entirely


class Fetcher:
    def __call__(self, day: date, bbox: tuple):
        lon = GRID["x0"] + np.arange(GRID["nx"]) * GRID["dx"]
        arr = np.ones((GRID["ny"], GRID["nx"]), dtype="float32")
        arr[:, lon < 35.5] = 8.0
        return arr, dict(GRID)


@pytest.fixture
def env(monkeypatch, tmp_path, login):
    """DB (or skip) + a published TST product; yields (client, version)."""
    monkeypatch.setattr(settings, "database_url", DB_URL)
    monkeypatch.setattr(settings, "weather_cache_dir", str(tmp_path))
    try:
        from app import auth, crops, publish, quotes
        from app.db import connect, init_schema

        init_schema()
        crops.init_schema()
        crops.seed_if_empty()
        publish.init_schema()
        quotes.init_schema()
        auth.init_schema()
    except Exception:
        pytest.skip("PostgreSQL not reachable — run `docker compose up db`")

    store = WeatherStore(cache_dir=tmp_path, fetch_day=Fetcher())
    for y in YEARS:
        store.ensure_year("TST", y, BBOX)

    zm_name = f"pytest-tst-{uuid.uuid4().hex[:8]}"
    draft_id = f"pytest-{uuid.uuid4().hex[:8]}"
    definition = propose_product(store, "TST", YEARS, ZONE_GEOJSON, STAGES,
                                 plant_start="03-15", sum_insured=10000)
    with connect() as conn:
        conn.execute(
            """INSERT INTO zone_map_versions
               (name, country, run_id, params, homogeneity, geojson, approved_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (zm_name, "TST", "test-run",
             json.dumps({"years": YEARS, "n_clusters": 2, "sensitivity": 1.25, "admin_snap": False}),
             json.dumps({"1": None, "2": None}), json.dumps(ZONE_GEOJSON), "pytest"))
        conn.execute(
            """INSERT INTO product_drafts
               (id, country, zone_map, crop, crop_version, season, years,
                sum_insured, definition, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (draft_id, "TST", zm_name, "maize", 1, "long_rains",
             json.dumps(YEARS), 10000, json.dumps(definition), "pytest"))

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    login(client, "admin")  # publish + quote + demand-signal routes are role-gated
    pub = client.post(f"/products/drafts/{draft_id}/publish",
                      json={"distribution": "gamma", "loadings": DEFAULT_LOADINGS}).json()

    yield client, pub, tmp_path

    with connect() as conn:
        conn.execute("DELETE FROM quotes WHERE country = 'TST'")
        conn.execute("DELETE FROM demand_signals WHERE country = 'TST'")
        conn.execute("DELETE FROM published_rates WHERE country = 'TST'")
        conn.execute("DELETE FROM published_products WHERE country = 'TST'")
        conn.execute("DELETE FROM product_drafts WHERE country = 'TST'")
        conn.execute("DELETE FROM zone_map_versions WHERE name = %s", (zm_name,))
        conn.execute("DELETE FROM users WHERE created_by = 'test'")


def _quote(client, pin, **over):
    body = {"country": "TST", "crop": "maize", "season": "long_rains",
            "sum_insured": 10000, **pin}
    body.update(over)
    return client.post("/quotes", json=body).json()


def test_pin_resolves_to_zone_and_prices_from_published_rate(env):
    client, pub, _ = env
    rate_by_zone = {r["zone"]: r["premium_rate"] for r in pub["rates"]}

    q1 = _quote(client, PIN_Z1, sum_insured=15000)
    assert q1["status"] == "quoted"
    assert q1["zone"] == 1
    assert q1["premium_rate"] == rate_by_zone[1]
    assert q1["premium"] == round(rate_by_zone[1] / 100 * 15000, 2)

    q2 = _quote(client, PIN_Z2)
    assert q2["zone"] == 2


def test_quote_traces_to_exact_product_version(env):
    client, pub, _ = env
    q = _quote(client, PIN_Z1)
    assert q["product_id"] == pub["id"]
    assert q["product_version"] == pub["version"]
    got = client.get(f"/quotes/{q['reference']}").json()
    assert got["reference"] == q["reference"]
    assert got["product_version"] == pub["version"]
    assert got["zone"] == 1


def test_outside_map_returns_friendly_message_and_demand_signal(env):
    client, _, _ = env
    q = _quote(client, PIN_OUT, admin_area="Faraway")
    assert q["status"] == "outside_coverage"
    assert q["demand_signal_id"]
    signals = client.get("/demand-signals", params={"country": "TST"}).json()
    assert any(s["reason"] == "outside_coverage" and s["admin_area"] == "Faraway" for s in signals)


def test_no_product_returns_friendly_message_and_demand_signal(env):
    client, _, _ = env
    q = _quote(client, PIN_Z1, crop="sorghum")
    assert q["status"] == "no_product"
    assert q["demand_signal_id"]
    signals = client.get("/demand-signals", params={"country": "TST"}).json()
    assert any(s["reason"] == "no_product" and s["crop"] == "sorghum" for s in signals)


def test_village_pick_matches_equivalent_pin(env):
    """A district's representative point (what the picker sends) quotes the same
    as a plain pin at that point — same zone, same rate."""
    client, _, tmp_path = env
    # Synthetic GADM: one district wholly inside zone 1.
    gadm = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"NAME_1": "West", "NAME_2": "Westville"},
         "geometry": {"type": "Polygon", "coordinates": [
             [[34.1, -0.9], [35.2, -0.9], [35.2, 1.9], [34.1, 1.9], [34.1, -0.9]]]}}]}
    gdir = Path(tmp_path) / "gadm"
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "TST_2.json").write_text(json.dumps(gadm))

    areas = client.get("/quote-areas", params={"country": "TST"}).json()
    assert len(areas) == 1 and areas[0]["name"] == "Westville"
    area = areas[0]

    via_village = _quote(client, {"lat": area["lat"], "lon": area["lon"]}, admin_area=area["name"])
    via_pin = _quote(client, {"lat": area["lat"], "lon": area["lon"]})
    assert via_village["status"] == "quoted"
    assert via_village["zone"] == via_pin["zone"] == 1
    assert via_village["premium_rate"] == via_pin["premium_rate"]


def test_quote_is_fast(env):
    """The pin→premium path answers well under a second (in-process)."""
    import time

    client, _, _ = env
    t = time.perf_counter()
    _quote(client, PIN_Z1)
    assert (time.perf_counter() - t) < 1.0
