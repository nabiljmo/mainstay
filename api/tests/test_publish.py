"""Publish / registry tests (issue 011).

Like the approval tests, these need the docker-compose PostgreSQL because
immutability and versioning are enforced by the database; they skip when no DB
is reachable. Weather is a small synthetic CHIRPS cache (no network), and a
synthetic country "TST" keeps setup and teardown isolated from real data.
"""

import json
import uuid
from datetime import date

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


class Fetcher:
    """West (lon<35.5): 8mm/day. East: 1mm/day."""

    def __call__(self, day: date, bbox: tuple):
        lon_axis = GRID["x0"] + np.arange(GRID["nx"]) * GRID["dx"]
        arr = np.ones((GRID["ny"], GRID["nx"]), dtype="float32")
        arr[:, lon_axis < 35.5] = 8.0
        return arr, dict(GRID)


@pytest.fixture
def env(monkeypatch, tmp_path, login):
    """A live DB (or skip) + synthetic weather + an approved zone map + a draft.
    Yields (client, draft_id). Cleans up every TST row afterwards."""
    monkeypatch.setattr(settings, "database_url", DB_URL)
    monkeypatch.setattr(settings, "weather_cache_dir", str(tmp_path))

    try:
        from app import auth, crops, publish
        from app.db import connect, init_schema

        init_schema()
        crops.init_schema()
        crops.seed_if_empty()
        publish.init_schema()
        auth.init_schema()
    except Exception:
        pytest.skip("PostgreSQL not reachable — run `docker compose up db`")

    # Synthetic CHIRPS cache the app's _store() will read.
    store = WeatherStore(cache_dir=tmp_path, fetch_day=Fetcher())
    for y in YEARS:
        store.ensure_year("TST", y, BBOX)

    zm_name = f"pytest-tst-{uuid.uuid4().hex[:8]}"
    draft_id = f"pytest-{uuid.uuid4().hex[:8]}"
    definition = propose_product(
        store, "TST", YEARS, ZONE_GEOJSON, STAGES, plant_start="03-15", sum_insured=10000,
    )

    with connect() as conn:
        conn.execute(
            """INSERT INTO zone_map_versions
               (name, country, run_id, params, homogeneity, geojson, approved_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (zm_name, "TST", "test-run",
             json.dumps({"country": "TST", "years": YEARS, "n_clusters": 2,
                         "sensitivity": 1.25, "admin_snap": False, "seed": 1}),
             json.dumps({"1": None, "2": None}), json.dumps(ZONE_GEOJSON), "pytest"),
        )
        conn.execute(
            """INSERT INTO product_drafts
               (id, country, zone_map, crop, crop_version, season, years,
                sum_insured, definition, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (draft_id, "TST", zm_name, "maize", 1, "long_rains",
             json.dumps(YEARS), 10000, json.dumps(definition), "pytest"),
        )

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    login(client, "admin")  # publish/read routes are role-gated now
    yield client, draft_id, definition

    with connect() as conn:
        conn.execute("DELETE FROM published_rates WHERE country = 'TST'")
        conn.execute("DELETE FROM published_products WHERE country = 'TST'")
        conn.execute("DELETE FROM product_drafts WHERE country = 'TST'")
        conn.execute("DELETE FROM zone_map_versions WHERE name = %s", (zm_name,))
        conn.execute("DELETE FROM users WHERE created_by = 'test'")


def _publish(client, draft_id, **over):
    body = {"distribution": "gamma", "loadings": DEFAULT_LOADINGS, "published_by": "pytest"}
    body.update(over)
    return client.post(f"/products/drafts/{draft_id}/publish", json=body)


def test_publish_freezes_all_zones_and_records_rates(env):
    client, draft_id, definition = env
    r = _publish(client, draft_id)
    assert r.status_code == 200
    d = r.json()
    assert d["id"] == "TST-maize-long_rains-v1"
    assert d["version"] == 1
    assert d["n_zones"] == len(definition["zones"])
    assert {row["zone"] for row in d["rates"]} == {1, 2}
    for row in d["rates"]:
        assert row["premium_rate"] >= 0


def test_republishing_mints_a_new_version_never_overwrites(env):
    client, draft_id, _ = env
    first = _publish(client, draft_id).json()
    second = _publish(client, draft_id).json()
    assert first["version"] == 1
    assert second["version"] == 2
    assert first["id"] != second["id"]
    # Both versions remain in the registry — nothing was mutated in place.
    listed = {p["id"] for p in client.get("/products/published").json()}
    assert first["id"] in listed and second["id"] in listed


def test_published_product_is_immutable_at_api_level(env):
    client, draft_id, _ = env
    pid = _publish(client, draft_id).json()["id"]
    # There is no update or delete route: the API surface itself enforces
    # immutability. Assert the mutating verbs are not accepted.
    assert client.put(f"/products/published/{pid}", json={"version": 99}).status_code in (404, 405)
    assert client.delete(f"/products/published/{pid}").status_code in (404, 405)
    # And the record still reads back exactly as published.
    got = client.get(f"/products/published/{pid}").json()
    assert got["id"] == pid and got["version"] == 1


def test_rate_table_queryable_by_key_and_returns_latest(env):
    client, draft_id, _ = env
    _publish(client, draft_id)                      # v1
    v2 = _publish(client, draft_id).json()["id"]    # v2

    all_rates = client.get(
        "/rates", params={"country": "TST", "crop": "maize", "season": "long_rains"}).json()
    assert {r["zone"] for r in all_rates} == {1, 2}
    assert all(r["product_id"] == v2 for r in all_rates)   # latest version wins

    one = client.get("/rates", params={
        "country": "TST", "crop": "maize", "season": "long_rains", "zone": 1}).json()
    assert len(one) == 1 and one[0]["zone"] == 1


def test_published_rate_matches_the_priced_number(env):
    """Golden: the frozen rate equals a direct re-computation of the same
    terms — the actuary gets exactly the number they saw."""
    client, draft_id, definition = env
    from app.economics import compute_zone_economics

    store = WeatherStore(cache_dir=settings.weather_cache_dir)
    phases = definition["zones"]["1"]["phases"] if "1" in definition["zones"] \
        else definition["zones"][1]["phases"]
    econ = compute_zone_economics(
        store, "TST", ZONE_GEOJSON, YEARS, "03-15", 10000, 1, phases,
        distribution="gamma", loadings=DEFAULT_LOADINGS, explanations=False,
    )
    published = _publish(client, draft_id).json()
    zone1 = next(r for r in published["rates"] if r["zone"] == 1)
    assert zone1["premium_rate"] == econ["price"]["premium_rate"]
    assert zone1["gross_premium"] == econ["price"]["gross_premium"]


def test_assumption_sheet_lists_dataset_and_method(env):
    client, draft_id, _ = env
    pid = _publish(client, draft_id).json()["id"]
    html = client.get(f"/products/published/{pid}/assumption-sheet")
    assert html.status_code == 200
    body = html.text
    assert "CHIRPS" in body
    assert "max(burning cost, modelled EL)" in body
    assert "Assumption Sheet" in body
