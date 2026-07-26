"""Binding / policy-register tests (issue 015). Needs docker-compose PostgreSQL
(skips otherwise). Publishes a synthetic TST product, quotes as an agent, then
binds — checking two-level integrity, statuses, agent scoping, and that PII is
ciphertext at rest."""

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
PIN_Z1 = {"lat": 0.5, "lon": 34.5}
PIN_Z2 = {"lat": 0.5, "lon": 36.5}
FARMER = {"name": "Amina Otieno", "phone": "+254700111222", "gender": "F", "national_id": "12345678"}


class Fetcher:
    def __call__(self, day, bbox):
        lon = GRID["x0"] + np.arange(GRID["nx"]) * GRID["dx"]
        arr = np.ones((GRID["ny"], GRID["nx"]), dtype="float32")
        arr[:, lon < 35.5] = 8.0
        return arr, dict(GRID)


@pytest.fixture
def env(monkeypatch, tmp_path, login):
    """DB (or skip) + a published TST product. Yields (make_agent, pub)."""
    monkeypatch.setattr(settings, "database_url", DB_URL)
    monkeypatch.setattr(settings, "weather_cache_dir", str(tmp_path))
    try:
        from app import auth, crops, policies, publish, quotes
        from app.db import connect, init_schema

        init_schema(); crops.init_schema(); crops.seed_if_empty()
        publish.init_schema(); quotes.init_schema(); policies.init_schema(); auth.init_schema()
    except Exception:
        pytest.skip("PostgreSQL not reachable — run `docker compose up db`")

    store = WeatherStore(cache_dir=tmp_path, fetch_day=Fetcher())
    for y in YEARS:
        store.ensure_year("TST", y, BBOX)
    zm = f"pytest-tst-{uuid.uuid4().hex[:8]}"
    did = f"pytest-{uuid.uuid4().hex[:8]}"
    definition = propose_product(store, "TST", YEARS, ZONE_GEOJSON, STAGES, "03-15", 10000)
    with connect() as conn:
        conn.execute("""INSERT INTO zone_map_versions
            (name, country, run_id, params, homogeneity, geojson, approved_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (zm, "TST", "r", json.dumps({"years": YEARS, "n_clusters": 2, "admin_snap": False}),
             json.dumps({"1": None, "2": None}), json.dumps(ZONE_GEOJSON), "pytest"))
        conn.execute("""INSERT INTO product_drafts
            (id, country, zone_map, crop, crop_version, season, years, sum_insured, definition, created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (did, "TST", zm, "maize", 1, "long_rains", json.dumps(YEARS),
             10000, json.dumps(definition), "pytest"))

    from fastapi.testclient import TestClient

    from app.main import app

    admin = TestClient(app); login(admin, "admin")
    pub = admin.post(f"/products/drafts/{did}/publish",
                     json={"distribution": "gamma", "loadings": DEFAULT_LOADINGS}).json()

    def make_agent():
        c = TestClient(app); login(c, "agent")
        return c

    yield make_agent, pub, admin

    with connect() as conn:
        conn.execute("DELETE FROM policy_schedule s USING master_policies m "
                     "WHERE s.master_policy_id = m.id AND m.country = 'TST'")
        conn.execute("DELETE FROM master_policies WHERE country = 'TST'")
        conn.execute("DELETE FROM quotes WHERE country = 'TST'")
        conn.execute("DELETE FROM demand_signals WHERE country = 'TST'")
        conn.execute("DELETE FROM published_rates WHERE country = 'TST'")
        conn.execute("DELETE FROM published_products WHERE country = 'TST'")
        conn.execute("DELETE FROM product_drafts WHERE country = 'TST'")
        conn.execute("DELETE FROM zone_map_versions WHERE name = %s", (zm,))
        conn.execute("DELETE FROM users WHERE created_by = 'test'")


def _quote(client, pin=PIN_Z1, si=10000):
    return client.post("/quotes", json={"country": "TST", "crop": "maize",
                       "season": "long_rains", "sum_insured": si, **pin}).json()


def test_individual_bind_creates_master_and_one_schedule_row(env):
    make_agent, pub, admin = env
    a = make_agent()
    q = _quote(a)
    r = a.post("/policies", json={"sale_type": "individual",
               "entries": [{"quote_reference": q["reference"], "farmer": FARMER}]})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["sale_type"] == "individual" and p["farmers"] == 1
    assert p["status"] == "draft"
    assert p["total_premium"] == q["premium"]

    detail = a.get(f"/policies/{p['id']}").json()
    assert len(detail["schedule"]) == 1
    assert detail["schedule"][0]["farmer"]["name"] == "Amina Otieno"  # decrypted back


# ----- sales cut-off (anti-selection) -----

def test_covered_season_before_cutoff_is_this_year():
    from datetime import date

    from app.policies import covered_season

    # Planting 03-15, 14-day buffer → cutoff 03-01. A February bind is in time.
    year, cutoff = covered_season("03-15", date(2026, 2, 1))
    assert year == 2026 and cutoff == date(2026, 3, 1)


def test_covered_season_after_cutoff_rolls_to_next_year():
    from datetime import date

    from app.policies import covered_season

    # A mid-season bind (June) is past this year's cutoff → covers next season.
    year, cutoff = covered_season("03-15", date(2026, 6, 20))
    assert year == 2027 and cutoff == date(2027, 3, 1)


def test_policy_document_renders_and_is_access_scoped(env):
    make_agent, pub, admin = env
    a = make_agent()
    q = _quote(a)
    p = a.post("/policies", json={"sale_type": "individual",
               "entries": [{"quote_reference": q["reference"], "farmer": FARMER}]}).json()

    doc = a.get(f"/policies/{p['id']}/document")
    assert doc.status_code == 200
    assert doc.headers["content-type"].startswith("text/html")
    body = doc.text
    assert p["id"] in body
    assert "Policy Schedule" in body
    assert "Amina Otieno" in body                 # the insured, decrypted for the owner
    assert "Drought cover" in body                # the cover-type glossary
    assert "Basis risk" in body                   # generic index-insurance terms are present
    assert "draft for review" in body             # clearly marked as pilot draft, not final

    other = make_agent()                          # a different agent can't read it
    assert other.get(f"/policies/{p['id']}/document").status_code == 403


def test_bind_captures_email_and_send_gates_cleanly(env):
    make_agent, pub, admin = env
    a = make_agent()
    q = _quote(a)
    p = a.post("/policies", json={"sale_type": "individual",
               "entries": [{"quote_reference": q["reference"],
                            "farmer": {**FARMER, "email": "farmer@example.com"}}]}).json()

    # Email captured, encrypted, decrypted back for the owner.
    detail = a.get(f"/policies/{p['id']}").json()
    assert detail["schedule"][0]["farmer"]["email"] == "farmer@example.com"

    # With no mailer configured, sending is a clean no-op — it never raises.
    from app.notify import send_policy_documents

    out = send_policy_documents(p["id"])
    assert out["sent"] == 0 and out["reason"] == "email not configured"


def test_send_skips_when_no_email_on_file(env):
    make_agent, pub, admin = env
    a = make_agent()
    q = _quote(a)
    p = a.post("/policies", json={"sale_type": "individual",
               "entries": [{"quote_reference": q["reference"], "farmer": FARMER}]}).json()
    from app.notify import send_policy_documents

    out = send_policy_documents(p["id"])
    assert out["sent"] == 0 and out["reason"] == "no email on file"


def test_policy_document_pdf_is_a_pdf(env):
    make_agent, pub, admin = env
    a = make_agent()
    q = _quote(a)
    p = a.post("/policies", json={"sale_type": "individual",
               "entries": [{"quote_reference": q["reference"], "farmer": FARMER}]}).json()
    try:
        from app.policies import policy_document_pdf

        pdf = policy_document_pdf(p["id"])
    except Exception as e:  # weasyprint's system libs aren't present in every env
        pytest.skip(f"weasyprint not usable here: {e}")
    assert pdf and pdf[:4] == b"%PDF"


def test_bind_stamps_the_covered_season(env):
    from datetime import date

    from app.policies import covered_season

    make_agent, pub, admin = env
    a = make_agent()
    q = _quote(a)
    r = a.post("/policies", json={"sale_type": "individual",
               "entries": [{"quote_reference": q["reference"], "farmer": FARMER}]})
    assert r.status_code == 200, r.text
    p = r.json()
    expected_year, _ = covered_season("03-15", date.today())  # product plants 03-15
    assert p["season_year"] == expected_year
    assert p["sales_cutoff"] is not None
    # The register carries it too.
    assert admin.get(f"/policies/{p['id']}").json()["season_year"] == expected_year


def test_partner_bind_bundles_many_farmers_under_one_master(env):
    make_agent, pub, admin = env
    a = make_agent()
    q1, q2 = _quote(a, PIN_Z1), _quote(a, PIN_Z2, si=5000)
    r = a.post("/policies", json={
        "sale_type": "partner", "partner_name": "Rift Valley Coop",
        "entries": [
            {"quote_reference": q1["reference"], "farmer": {**FARMER, "name": "Farmer One"}},
            {"quote_reference": q2["reference"], "farmer": {**FARMER, "name": "Farmer Two"}},
        ]})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["farmers"] == 2
    assert p["total_sum_insured"] == 15000
    assert p["partner_name"] == "Rift Valley Coop"
    assert {s["zone"] for s in a.get(f"/policies/{p['id']}").json()["schedule"]} == {1, 2}


def test_partner_sale_requires_partner_name(env):
    make_agent, *_ = env
    a = make_agent()
    q = _quote(a)
    r = a.post("/policies", json={"sale_type": "partner",
               "entries": [{"quote_reference": q["reference"], "farmer": FARMER}]})
    assert r.status_code == 400 and "partner name" in r.json()["detail"]


def test_individual_sale_must_be_exactly_one_farmer(env):
    make_agent, *_ = env
    a = make_agent()
    q1, q2 = _quote(a), _quote(a)
    r = a.post("/policies", json={"sale_type": "individual", "entries": [
        {"quote_reference": q1["reference"], "farmer": FARMER},
        {"quote_reference": q2["reference"], "farmer": FARMER}]})
    assert r.status_code == 400


def test_pii_is_ciphertext_at_rest(env):
    make_agent, *_ = env
    from app.crypto import decrypt
    from app.db import connect

    a = make_agent()
    q = _quote(a)
    p = a.post("/policies", json={"sale_type": "individual",
               "entries": [{"quote_reference": q["reference"], "farmer": FARMER}]}).json()
    with connect() as conn:
        name_enc, phone_enc = conn.execute(
            "SELECT name_enc, phone_enc FROM policy_schedule WHERE master_policy_id=%s",
            (p["id"],)).fetchone()
    assert name_enc != "Amina Otieno" and "Amina" not in name_enc  # stored encrypted
    assert "254700111222" not in phone_enc
    assert decrypt(name_enc) == "Amina Otieno"  # but recoverable with the key


def test_receipt_activates_then_cannot_reactivate(env):
    make_agent, *_ = env
    a = make_agent()
    q = _quote(a)
    p = a.post("/policies", json={"sale_type": "individual",
               "entries": [{"quote_reference": q["reference"], "farmer": FARMER}]}).json()
    rc = a.post(f"/policies/{p['id']}/receipt", json={"reference": "MPESA-XYZ", "date": "2026-04-01"})
    assert rc.status_code == 200 and rc.json()["status"] == "active"
    assert rc.json()["receipt_ref"] == "MPESA-XYZ"
    again = a.post(f"/policies/{p['id']}/receipt", json={"reference": "MPESA-2"})
    assert again.status_code == 400  # already active


def test_status_transitions(env):
    make_agent, pub, admin = env
    a = make_agent()
    q = _quote(a)
    p = a.post("/policies", json={"sale_type": "individual",
               "entries": [{"quote_reference": q["reference"], "farmer": FARMER}]}).json()
    # invalid: draft -> settled (must go through active)
    assert admin.post(f"/policies/{p['id']}/status", json={"status": "settled"}).status_code == 400
    a.post(f"/policies/{p['id']}/receipt", json={"reference": "R1"})  # -> active
    assert admin.post(f"/policies/{p['id']}/status", json={"status": "settled"}).json()["status"] == "settled"
    # agents cannot drive status transitions
    q2 = _quote(a)
    p2 = a.post("/policies", json={"sale_type": "individual",
                "entries": [{"quote_reference": q2["reference"], "farmer": FARMER}]}).json()
    assert a.post(f"/policies/{p2['id']}/status", json={"status": "expired"}).status_code == 403


def test_agent_scoping_and_operations_visibility(env):
    make_agent, pub, admin = env
    a, b = make_agent(), make_agent()
    q = _quote(a)
    p = a.post("/policies", json={"sale_type": "individual",
               "entries": [{"quote_reference": q["reference"], "farmer": FARMER}]}).json()

    assert a.get(f"/policies/{p['id']}").status_code == 200          # owner
    assert b.get(f"/policies/{p['id']}").status_code == 403          # other agent
    assert all(m["id"] != p["id"] for m in b.get("/policies").json())  # not in B's register
    assert any(m["id"] == p["id"] for m in admin.get("/policies").json())  # ops/admin see all


def test_no_orphan_schedule_rows(env):
    """A schedule row cannot exist without its master (FK integrity)."""
    make_agent, *_ = env
    from app.db import connect

    with connect() as conn:
        with pytest.raises(Exception):
            conn.execute(
                """INSERT INTO policy_schedule
                   (master_policy_id, zone, sum_insured, premium_rate, premium, name_enc, phone_enc)
                   VALUES ('NO-SUCH-MASTER', 1, 100, 10, 10, 'x', 'y')""")


def test_register_filters(env):
    make_agent, pub, admin = env
    a = make_agent()
    q = _quote(a, PIN_Z1)
    p = a.post("/policies", json={"sale_type": "individual",
               "entries": [{"quote_reference": q["reference"], "farmer": FARMER}]}).json()
    assert any(m["id"] == p["id"] for m in admin.get("/policies", params={"product_id": pub["id"]}).json())
    assert any(m["id"] == p["id"] for m in admin.get("/policies", params={"zone": 1}).json())
    assert all(m["id"] != p["id"] for m in admin.get("/policies", params={"zone": 2}).json())
    assert all(m["id"] != p["id"] for m in admin.get("/policies", params={"status": "active"}).json())
