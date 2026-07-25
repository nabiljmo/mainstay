"""Payout run tests (issue 017).

End-to-end against PostgreSQL (skips when unreachable): a synthetic season is
settled, then the payout run is reviewed, released, and exported. Asserts the
readiness gate, the >3x-expected-loss anomaly path, one-farmer-once, the policy
flip to settled, the release lock, and the exported file byte-for-byte.
"""

import json
import uuid
from datetime import date

import numpy as np
import pytest

from app.config import settings
from app.crypto import encrypt
from app.payout import build_payout_run, release_payout_run, render_payout_file
from app.settlement import run_settlement_sweep
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
    """West (zone 1): 8mm/day → wet, no payout. East (zone 2): 1mm/day → pays 70%."""

    def __call__(self, day: date, bbox: tuple):
        lon_axis = GRID["x0"] + np.arange(GRID["nx"]) * GRID["dx"]
        arr = np.ones((GRID["ny"], GRID["nx"]), dtype="float32")
        arr[:, lon_axis < 35.5] = 8.0
        return arr, dict(GRID)


@pytest.fixture
def env(monkeypatch, tmp_path, login):
    """DB (or skip) + synthetic CHIRPS + a published product with rates, plus one
    active master policy carrying three farmers (zone 1, and two in zone 2).
    Policies are dated into 2023 so the season year derives to 2023."""
    monkeypatch.setattr(settings, "database_url", DB_URL)
    monkeypatch.setattr(settings, "weather_cache_dir", str(tmp_path))

    try:
        from app import auth, payout, policies, publish, settlement
        from app.db import connect, init_schema

        init_schema()
        publish.init_schema()
        policies.init_schema()
        settlement.init_schema()
        payout.init_schema()
        auth.init_schema()
    except Exception:
        pytest.skip("PostgreSQL not reachable — run `docker compose up db`")

    store = WeatherStore(cache_dir=tmp_path, fetch_day=Fetcher())
    store.ensure_year("TST", SEASON_YEAR, BBOX)

    zm_name = f"pytest-tst-{uuid.uuid4().hex[:8]}"
    product_id = f"TST-maize-long_rains-{uuid.uuid4().hex[:6]}"
    master_id = f"MP-TST-{uuid.uuid4().hex[:8]}"
    # Known phones so the exported file can be asserted byte-for-byte.
    phones = {"z1": "254700000001", "z2a": "254700000002", "z2b": "254700000003"}

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
        # Priced expected loss per zone — high enough that 7 000 is NOT an anomaly
        # by default (3 x 3 000 = 9 000 > 7 000). The anomaly test lowers it.
        for zone, el in ((1, 500.0), (2, 3000.0)):
            conn.execute(
                """INSERT INTO published_rates
                   (product_id, country, crop, season, zone, sum_insured,
                    premium_rate, gross_premium, expected_loss, burning_cost,
                    technical_el, quality_flag)
                   VALUES (%s,'TST','maize','long_rains',%s,%s,%s,%s,%s,%s,%s,'green')""",
                (product_id, zone, 10000.0, 10.0, 1000.0, el, el, el))
        conn.execute(
            """INSERT INTO master_policies
               (id, sale_type, partner_name, product_id, country, crop, season,
                status, total_sum_insured, total_premium, created_by, created_at)
               VALUES (%s,'partner','ACME',%s,'TST','maize','long_rains',
                       'active',%s,%s,'test','2023-05-15 00:00:00+00')""",
            (master_id, product_id, 13000.0, 1300.0))
        for zone, si, phone in ((1, 5000.0, phones["z1"]),
                                (2, 5000.0, phones["z2a"]),
                                (2, 3000.0, phones["z2b"])):
            conn.execute(
                """INSERT INTO policy_schedule
                   (master_policy_id, quote_reference, zone, sum_insured,
                    premium_rate, premium, name_enc, phone_enc)
                   VALUES (%s,NULL,%s,%s,%s,%s,%s,%s)""",
                (master_id, zone, si, 10.0, si * 0.1,
                 encrypt("Farmer"), encrypt(phone)))

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    login(client, "operations")
    yield client, product_id, master_id, store

    with connect() as conn:
        conn.execute("DELETE FROM payout_lines WHERE run_id LIKE %s", (f"PR-{product_id}%",))
        conn.execute("DELETE FROM payout_runs WHERE product_id = %s", (product_id,))
        conn.execute("DELETE FROM settlements WHERE product_id = %s", (product_id,))
        conn.execute("DELETE FROM policy_schedule WHERE master_policy_id = %s", (master_id,))
        conn.execute("DELETE FROM master_policies WHERE id = %s", (master_id,))
        conn.execute("DELETE FROM published_rates WHERE product_id = %s", (product_id,))
        conn.execute("DELETE FROM published_products WHERE id = %s", (product_id,))
        conn.execute("DELETE FROM zone_map_versions WHERE name = %s", (zm_name,))
        conn.execute("DELETE FROM users WHERE created_by = 'test'")


def _settle(store, product_id):
    run_settlement_sweep(store, product_id=product_id, season_year=SEASON_YEAR,
                         today=date(2023, 5, 1))


# --------------------------------------------------------------- readiness gate

def test_not_ready_before_phases_are_settled(env):
    client, product_id, _, _ = env
    review = client.get("/payouts/run", params={"product_id": product_id}).json()
    assert review["ready"] is False
    assert {p["zone"] for p in review["pending"]} == {1, 2}


def test_release_blocked_until_settled(env):
    client, product_id, _, _ = env
    r = client.post("/payouts/release", json={"product_id": product_id, "confirm": True})
    assert r.status_code == 400
    assert "not ready" in r.json()["detail"] or "not every phase" in r.json()["detail"]


# ------------------------------------------------------------- review + amounts

def test_review_totals_and_per_farmer_amounts(env):
    client, product_id, _, store = env
    _settle(store, product_id)
    review = client.get("/payouts/run", params={"product_id": product_id}).json()

    assert review["ready"] is True
    assert review["season_year"] == SEASON_YEAR
    # Zone 2 pays 70% of sum insured: 5 000 → 3 500, 3 000 → 2 100. Zone 1 pays 0.
    assert review["total_amount"] == pytest.approx(5600.0)
    assert review["farmer_count"] == 2          # only the two paid farmers
    assert review["total_farmers"] == 3
    assert review["largest"][0]["amount"] == pytest.approx(3500.0)
    z2 = next(z for z in review["zones"] if z["zone"] == 2)
    assert z2["zone_payout"] == pytest.approx(5600.0)
    assert z2["anomaly"] is False               # 7 000 < 3 x 3 000


def test_anomaly_flag_when_zone_pays_over_three_times_el(env):
    client, product_id, _, store = env
    _settle(store, product_id)
    from app.db import connect
    with connect() as conn:  # drop zone 2's priced EL so 7 000 > 3 x 1 000
        conn.execute(
            "UPDATE published_rates SET expected_loss=1000 WHERE product_id=%s AND zone=2",
            (product_id,))

    review = client.get("/payouts/run", params={"product_id": product_id}).json()
    z2 = next(z for z in review["zones"] if z["zone"] == 2)
    assert z2["anomaly"] is True
    assert any(a["zone"] == 2 and a["multiple"] == pytest.approx(7.0)
               for a in review["anomalies"])


# ------------------------------------------------------------- release + lock

def test_release_requires_confirmation(env):
    client, product_id, _, store = env
    _settle(store, product_id)
    r = client.post("/payouts/release", json={"product_id": product_id, "confirm": False})
    assert r.status_code == 400
    assert "confirm" in r.json()["detail"].lower()


def test_release_locks_flips_policies_and_is_idempotent(env):
    client, product_id, master_id, store = env
    _settle(store, product_id)

    rel = client.post("/payouts/release",
                      json={"product_id": product_id, "confirm": True})
    assert rel.status_code == 200, rel.text
    run = rel.json()
    assert run["status"] == "released"
    assert run["farmer_count"] == 2
    assert run["total_amount"] == pytest.approx(5600.0)
    assert run["released_by"]

    # The policy flipped to settled.
    got = client.get(f"/policies/{master_id}").json()
    assert got["status"] == "settled"

    # A second release is refused — the season closes exactly once.
    again = client.post("/payouts/release",
                        json={"product_id": product_id, "confirm": True})
    assert again.status_code == 400
    assert "already released" in again.json()["detail"]


# --------------------------------------------------------------- exported file

def test_exported_file_is_exact_and_one_line_per_paid_farmer(env):
    client, product_id, master_id, store = env
    _settle(store, product_id)
    client.post("/payouts/release", json={"product_id": product_id, "confirm": True})

    run_id = f"PR-{product_id}-{SEASON_YEAR}"
    resp = client.get(f"/payouts/runs/{run_id}/file")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")

    ev = f"EV-{product_id}-{SEASON_YEAR}-Z2"
    expected = (
        "policy_number,phone,zone,amount,evidence_reference\n"
        f"{master_id},254700000002,2,3500.00,{ev}\n"
        f"{master_id},254700000003,2,2100.00,{ev}\n"
    )
    assert resp.text == expected
    # Exactly the two paid farmers — the zone-1 farmer (0 payout) is not in the file.
    assert resp.text.count("\n") == 3  # header + 2 rows
