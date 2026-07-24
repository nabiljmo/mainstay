"""Approval-gate tests. They need a real PostgreSQL (the docker compose one on
localhost:5432) because immutability is enforced by the database's unique
constraint; when no database is reachable the tests skip rather than fake it."""

import json
import uuid

import pytest

from app.config import settings

DB_URL = "postgresql://aez:aez@localhost:5432/aez"


@pytest.fixture
def db_or_skip(monkeypatch):
    monkeypatch.setattr(settings, "database_url", DB_URL)
    try:
        from app.db import connect, init_schema

        init_schema()
    except Exception:
        pytest.skip("PostgreSQL not reachable — run `docker compose up db`")
    yield
    with connect() as conn:
        conn.execute("DELETE FROM zone_map_versions WHERE name LIKE 'pytest-%'")


@pytest.fixture
def draft_run(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "weather_cache_dir", str(tmp_path))
    run_dir = tmp_path / "zoning" / "KEN"
    run_dir.mkdir(parents=True)
    record = {
        "run_id": "test-run",
        "created_at": "2026-01-01T00:00:00",
        "params": {"country": "KEN", "years": [2021], "n_clusters": 3, "sensitivity": 1.25, "seed": 1},
        "homogeneity": {"1": None, "2": None, "3": None},
        "geojson": {"type": "FeatureCollection", "features": []},
    }
    (run_dir / "test-run.json").write_text(json.dumps(record))
    return "test-run"


def test_approve_freezes_and_records_audit(db_or_skip, draft_run):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    name = f"pytest-{uuid.uuid4().hex[:8]}"

    r = client.post(
        f"/zoning/runs/KEN/{draft_run}/approve",
        json={"name": name, "approved_by": "pytest"},
    )
    assert r.status_code == 200

    versions = client.get("/zone-maps", params={"country": "KEN"}).json()
    mine = next(v for v in versions if v["name"] == name)
    # Audit record completeness: who, when, and every parameter.
    assert mine["approved_by"] == "pytest"
    assert mine["approved_at"]
    assert mine["params"]["years"] == [2021]
    assert mine["params"]["n_clusters"] == 3
    assert mine["params"]["seed"] == 1


def test_approved_versions_are_immutable(db_or_skip, draft_run):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    name = f"pytest-{uuid.uuid4().hex[:8]}"

    first = client.post(
        f"/zoning/runs/KEN/{draft_run}/approve",
        json={"name": name, "approved_by": "pytest"},
    )
    assert first.status_code == 200

    again = client.post(
        f"/zoning/runs/KEN/{draft_run}/approve",
        json={"name": name, "approved_by": "someone-else"},
    )
    assert again.status_code == 409
    assert "immutable" in again.json()["detail"]
