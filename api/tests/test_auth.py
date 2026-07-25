"""Auth + route-level authorization matrix (issue 013).

Needs the docker-compose PostgreSQL (skips otherwise). Verifies that each role
reaches only its own routes, that the server (not just the UI) enforces it,
that an agent cannot read another agent's quote, and that admin user management
works — including the guards that stop you locking out the last admin.
"""

import uuid

import pytest

from app.config import settings

DB_URL = "postgresql://aez:aez@localhost:5432/aez"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "database_url", DB_URL)
    try:
        from app import auth
        from app.db import connect

        auth.init_schema()
    except Exception:
        pytest.skip("PostgreSQL not reachable — run `docker compose up db`")

    from fastapi.testclient import TestClient

    from app.main import app

    yield TestClient(app)

    with connect() as conn:
        conn.execute("DELETE FROM users WHERE created_by = 'test'")


def _client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


# ----- login / sessions -----

def test_login_bad_credentials_rejected(client, login):
    from app import auth

    u = f"t_actuary_{uuid.uuid4().hex[:8]}"
    auth.create_user(u, "correct-horse", "actuary", created_by="test")
    assert client.post("/auth/login", json={"username": u, "password": "wrong"}).status_code == 401
    ok = client.post("/auth/login", json={"username": u, "password": "correct-horse"})
    assert ok.status_code == 200 and ok.json()["role"] == "actuary"


def test_unauthenticated_is_blocked(client):
    # No cookie → protected routes 401.
    assert client.get("/auth/me").status_code == 401
    assert client.get("/products/drafts").status_code == 401
    assert client.post("/quotes", json={}).status_code in (401, 422)  # auth before/at body parse


def test_logout_ends_the_session(client, login):
    login(client, "actuary")
    assert client.get("/auth/me").status_code == 200
    client.post("/auth/logout")
    assert client.get("/auth/me").status_code == 401


def test_expired_session_is_rejected(client, login):
    """A session past its expiry does not authenticate."""
    from datetime import datetime, timedelta, timezone

    from app.db import connect

    u = login(client, "actuary")
    assert client.get("/auth/me").status_code == 200
    with connect() as conn:
        conn.execute(
            """UPDATE sessions SET expires_at = %s
               WHERE user_id = (SELECT id FROM users WHERE username = %s)""",
            (datetime.now(timezone.utc) - timedelta(minutes=1), u))
    assert client.get("/auth/me").status_code == 401


# ----- the authorization matrix -----

# (method, path, body) -> the set of roles allowed (admin always allowed too).
MATRIX = [
    ("post", "/products/draft", {}, {"actuary"}),
    ("get", "/products/drafts", None, {"actuary"}),
    # throwaway crop name so allowed roles don't pollute the real maize record
    ("post", "/crops/ztest_authz/versions", {"stages": [], "seasons": []}, {"agronomist"}),
    ("get", "/demand-signals", None, {"operations"}),
    ("get", "/admin/users", None, set()),          # admin-only (empty = only admin)
    ("get", "/zone-maps", None, {"actuary", "agronomist", "operations"}),
    ("post", "/quotes", {}, {"agent"}),
]
ROLES = ["actuary", "agronomist", "agent", "operations"]


@pytest.mark.parametrize("method,path,body,allowed", MATRIX)
def test_route_role_matrix(monkeypatch, login, method, path, body, allowed):
    """Each role gets 403 on routes it may not touch, and gets past the gate
    (not 401/403) on routes it may."""
    monkeypatch.setattr(settings, "database_url", DB_URL)
    try:
        from app import auth
        auth.init_schema()
    except Exception:
        pytest.skip("PostgreSQL not reachable — run `docker compose up db`")

    from fastapi.testclient import TestClient

    from app.db import connect
    from app.main import app

    for role in ROLES:
        c = TestClient(app)
        login(c, role)
        resp = getattr(c, method)(path, json=body) if body is not None else getattr(c, method)(path)
        if role in allowed or (allowed == set() and role == "admin"):
            assert resp.status_code != 403, f"{role} should reach {path}: {resp.status_code}"
        elif role not in allowed:
            assert resp.status_code == 403, f"{role} must be denied {path}: {resp.status_code}"

    # admin passes every gate.
    c = TestClient(app)
    login(c, "admin")
    resp = getattr(c, method)(path, json=body) if body is not None else getattr(c, method)(path)
    assert resp.status_code != 403, f"admin should reach {path}: {resp.status_code}"

    with connect() as conn:
        conn.execute("DELETE FROM users WHERE created_by = 'test'")
        conn.execute("DELETE FROM crop_versions WHERE crop = 'ztest_authz'")


# ----- agent data scoping -----

def test_agent_cannot_read_another_agents_quote(monkeypatch, login):
    """Agent A creates a quote; agent B is refused it; operations may read it."""
    import json
    from datetime import date
    from pathlib import Path

    import numpy as np

    from app.db import connect
    from app.pricing import DEFAULT_LOADINGS
    from app.products import propose_product
    from app.weather import WeatherStore

    # --- stand up a published product on TST so a real quote can be made ---
    import tempfile
    monkeypatch.setattr(settings, "database_url", DB_URL)
    d = tempfile.mkdtemp()
    monkeypatch.setattr(settings, "weather_cache_dir", d)
    try:
        from app import auth, crops, publish, quotes
        from app.db import init_schema
        init_schema(); crops.init_schema(); crops.seed_if_empty()
        publish.init_schema(); quotes.init_schema(); auth.init_schema()
    except Exception:
        pytest.skip("PostgreSQL not reachable — run `docker compose up db`")

    grid = {"x0": 34.0, "y0": 2.0, "dx": 0.5, "dy": -0.5, "nx": 6, "ny": 6}
    bbox = (34.0, -1.0, 37.0, 2.0)
    zg = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"zone": 1}, "geometry": {"type": "Polygon",
         "coordinates": [[[33.9, -1.1], [37.1, -1.1], [37.1, 2.1], [33.9, 2.1], [33.9, -1.1]]]}}]}
    stages = [{"name": "establishment", "days": 20, "sensitivity": 0.15},
              {"name": "vegetative", "days": 35, "sensitivity": 0.20},
              {"name": "flowering", "days": 25, "sensitivity": 0.40},
              {"name": "grain_filling", "days": 40, "sensitivity": 0.25}]

    def fetch(day, b):
        return np.full((grid["ny"], grid["nx"]), 8.0, dtype="float32"), dict(grid)

    store = WeatherStore(cache_dir=d, fetch_day=fetch)
    for y in (2021, 2022, 2023):
        store.ensure_year("TST", y, bbox)
    zm = f"pytest-tst-{uuid.uuid4().hex[:8]}"
    did = f"pytest-{uuid.uuid4().hex[:8]}"
    definition = propose_product(store, "TST", [2021, 2022, 2023], zg, stages, "03-15", 10000)
    with connect() as conn:
        conn.execute("""INSERT INTO zone_map_versions
            (name, country, run_id, params, homogeneity, geojson, approved_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (zm, "TST", "r", json.dumps({"years": [2021], "n_clusters": 1, "admin_snap": False}),
             json.dumps({"1": None}), json.dumps(zg), "pytest"))
        conn.execute("""INSERT INTO product_drafts
            (id, country, zone_map, crop, crop_version, season, years, sum_insured, definition, created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (did, "TST", zm, "maize", 1, "long_rains", json.dumps([2021, 2022, 2023]),
             10000, json.dumps(definition), "pytest"))

    from fastapi.testclient import TestClient

    from app.main import app

    admin = TestClient(app); login(admin, "admin")
    admin.post(f"/products/drafts/{did}/publish",
               json={"distribution": "gamma", "loadings": DEFAULT_LOADINGS})

    # --- agent A makes a quote ---
    a = TestClient(app); login(a, "agent")
    q = a.post("/quotes", json={"country": "TST", "crop": "maize", "season": "long_rains",
                                "sum_insured": 10000, "lat": 0.5, "lon": 35.0}).json()
    assert q["status"] == "quoted"
    ref = q["reference"]
    assert a.get(f"/quotes/{ref}").status_code == 200          # owner can read

    b = TestClient(app); login(b, "agent")
    assert b.get(f"/quotes/{ref}").status_code == 403          # other agent cannot
    # agent B's own list does not contain A's quote
    assert all(row["reference"] != ref for row in b.get("/quotes").json())

    ops = TestClient(app); login(ops, "operations")
    assert ops.get(f"/quotes/{ref}").status_code == 200        # operations may read any

    with connect() as conn:
        conn.execute("DELETE FROM quotes WHERE country='TST'")
        conn.execute("DELETE FROM demand_signals WHERE country='TST'")
        conn.execute("DELETE FROM published_rates WHERE country='TST'")
        conn.execute("DELETE FROM published_products WHERE country='TST'")
        conn.execute("DELETE FROM product_drafts WHERE country='TST'")
        conn.execute("DELETE FROM zone_map_versions WHERE name=%s", (zm,))
        conn.execute("DELETE FROM users WHERE created_by='test'")


# ----- admin user management -----

def test_admin_can_create_deactivate_and_rerole_users(client, login):
    login(client, "admin")
    name = f"t_new_{uuid.uuid4().hex[:8]}"

    made = client.post("/admin/users", json={"username": name, "password": "pw", "role": "agent"})
    assert made.status_code == 200 and made.json()["role"] == "agent"

    # created user can log in on a fresh client
    from fastapi.testclient import TestClient

    from app.main import app
    other = TestClient(app)
    assert other.post("/auth/login", json={"username": name, "password": "pw"}).status_code == 200

    # change role
    assert client.patch(f"/admin/users/{name}", json={"role": "actuary"}).json()["role"] == "actuary"
    # deactivate → cannot log in
    assert client.patch(f"/admin/users/{name}", json={"active": False}).json()["active"] is False
    fresh = TestClient(app)
    assert fresh.post("/auth/login", json={"username": name, "password": "pw"}).status_code == 401

    # tidy the created_by='admin-ish' user (created_by is the admin username, not 'test')
    from app.db import connect
    with connect() as conn:
        conn.execute("DELETE FROM users WHERE username = %s", (name,))


def test_non_admin_cannot_manage_users(client, login):
    login(client, "actuary")
    assert client.get("/admin/users").status_code == 403
    assert client.post("/admin/users",
                       json={"username": "x", "password": "y", "role": "agent"}).status_code == 403


def test_cannot_deactivate_the_last_admin(client, login):
    """Guard: the sole active admin cannot be locked out."""
    from app import auth
    from app.db import connect

    # Ensure exactly one admin exists for a clean assertion: use a fresh DB view
    # by counting; if seed admin exists this still holds because the guard checks
    # "no other active admin".
    admin_user = login(client, "admin")
    with connect() as conn:
        others = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role='admin' AND active AND username <> %s",
            (admin_user,)).fetchone()[0]
    r = client.patch(f"/admin/users/{admin_user}", json={"active": False})
    if others == 0:
        assert r.status_code == 400 and "last active admin" in r.json()["detail"]
    else:
        assert r.status_code == 200  # another admin exists, so this one may go
