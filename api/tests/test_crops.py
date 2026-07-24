"""Crop Library tests — need the docker compose PostgreSQL, skip otherwise."""

import uuid

import pytest

from app.config import settings

DB_URL = "postgresql://aez:aez@localhost:5432/aez"


@pytest.fixture
def db_or_skip(monkeypatch):
    monkeypatch.setattr(settings, "database_url", DB_URL)
    try:
        from app import crops
        from app.db import init_schema

        init_schema()
        crops.init_schema()
    except Exception:
        pytest.skip("PostgreSQL not reachable — run `docker compose up db`")
    yield
    from app.db import connect

    with connect() as conn:
        conn.execute("DELETE FROM crop_versions WHERE crop LIKE 'pytest-%'")


def _stages():
    return [
        {"name": "establishment", "days": 20, "sensitivity": 0.15},
        {"name": "vegetative", "days": 35, "sensitivity": 0.20},
        {"name": "flowering", "days": 25, "sensitivity": 0.40},
        {"name": "grain_filling", "days": 40, "sensitivity": 0.25},
    ]


def _seasons():
    return [{"country": "KEN", "season": "long_rains", "plant_start": "03-15", "plant_end": "04-15"}]


def test_saving_increments_version(db_or_skip):
    from app import crops

    crop = f"pytest-{uuid.uuid4().hex[:8]}"
    v1 = crops.save_new_version(crop, _stages(), _seasons(), edited_by="a")
    v2 = crops.save_new_version(crop, _stages(), _seasons(), edited_by="b")
    assert v1["version"] == 1
    assert v2["version"] == 2
    assert crops.latest_versions()  # smoke: query works


def test_old_version_is_immutable_snapshot(db_or_skip):
    from app import crops

    crop = f"pytest-{uuid.uuid4().hex[:8]}"
    crops.save_new_version(crop, _stages(), _seasons(), edited_by="a")

    changed = _stages()
    changed[2]["sensitivity"] = 0.99  # edit flowering
    crops.save_new_version(crop, changed, _seasons(), edited_by="b")

    v1 = crops.get_version(crop, 1)
    v2 = crops.get_version(crop, 2)
    # v1 keeps its original numbers; the edit lives only in v2.
    assert v1["stages"][2]["sensitivity"] == 0.40
    assert v2["stages"][2]["sensitivity"] == 0.99
    assert v1["edited_by"] == "a"
    assert v2["edited_by"] == "b"


def test_validate_flags_bad_sensitivity_and_duration():
    from app import crops

    bad_stages = [{"name": "x", "days": 5, "sensitivity": 0.5}]
    warnings = crops.validate(bad_stages, _seasons())
    assert any("sensitivity" in w for w in warnings)
    assert any("days" in w or "season" in w for w in warnings)


def test_good_record_has_no_warnings():
    from app import crops

    assert crops.validate(_stages(), _seasons()) == []


def test_maize_seed_is_wellformed():
    from app import crops

    seed = crops.MAIZE_SEED
    assert crops.validate(seed["stages"], seed["seasons"]) == []
    # Flowering is the most water-stress-sensitive stage.
    flowering = next(s for s in seed["stages"] if s["name"] == "flowering")
    assert flowering["sensitivity"] == max(s["sensitivity"] for s in seed["stages"])
