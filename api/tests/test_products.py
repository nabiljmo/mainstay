from datetime import date

import numpy as np
import pytest

from app.products import propose_product, zone_daily_series
from app.weather import WeatherStore

# Small country: 6x6 grid. Western half is a wet zone, eastern half dry.
GRID = {"x0": 34.0, "y0": 2.0, "dx": 0.5, "dy": -0.5, "nx": 6, "ny": 6}
BBOX = (34.0, -1.0, 37.0, 2.0)

STAGES = [
    {"name": "establishment", "days": 20, "sensitivity": 0.15},
    {"name": "vegetative", "days": 35, "sensitivity": 0.20},
    {"name": "flowering", "days": 25, "sensitivity": 0.40},
    {"name": "grain_filling", "days": 40, "sensitivity": 0.25},
]

# Two zones split down the middle (lon < 35.5 => zone 1, else zone 2).
ZONE_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"zone": 1},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[33.9, -1.1], [35.4, -1.1], [35.4, 2.1], [33.9, 2.1], [33.9, -1.1]]],
            },
        },
        {
            "type": "Feature",
            "properties": {"zone": 2},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[35.6, -1.1], [37.1, -1.1], [37.1, 2.1], [35.6, 2.1], [35.6, -1.1]]],
            },
        },
    ],
}


class Fetcher:
    """West (lon<35.5): 8mm every day. East: 1mm every day."""

    def __call__(self, day: date, bbox: tuple):
        lon_axis = GRID["x0"] + np.arange(GRID["nx"]) * GRID["dx"]
        arr = np.ones((GRID["ny"], GRID["nx"]), dtype="float32")
        arr[:, lon_axis < 35.5] = 8.0
        return arr, dict(GRID)


@pytest.fixture
def store(tmp_path):
    s = WeatherStore(cache_dir=tmp_path, fetch_day=Fetcher())
    for y in (2021, 2022, 2023):
        s.ensure_year("TST", y, BBOX)
    return s


def test_zone_series_area_average(store):
    series = zone_daily_series(store, "TST", 2021, ZONE_GEOJSON)
    assert set(series) == {1, 2}
    # Wet west zone averages 8mm/day, dry east 1mm/day.
    assert np.nanmean(series[1]) == pytest.approx(8.0)
    assert np.nanmean(series[2]) == pytest.approx(1.0)


def test_propose_product_shapes_and_triggers(store):
    product = propose_product(
        store, "TST", [2021, 2022, 2023], ZONE_GEOJSON, STAGES,
        plant_start="03-15", sum_insured=10000,
    )
    assert set(product["zones"]) == {1, 2}
    z1 = product["zones"][1]["phases"]
    assert [p["name"] for p in z1] == ["establishment", "vegetative", "flowering", "grain_filling"]
    # Flowering carries the biggest limit (0.40 sensitivity).
    flowering = next(p for p in z1 if p["name"] == "flowering")
    assert flowering["limit"] == pytest.approx(4000)
    # Phase limits sum to the sum insured.
    assert sum(p["limit"] for p in z1) == pytest.approx(10000)


def test_default_cover_types_assigned(store):
    product = propose_product(
        store, "TST", [2021, 2022, 2023], ZONE_GEOJSON, STAGES,
        plant_start="03-15", sum_insured=10000,
    )
    covers = {p["name"]: p["cover_type"] for p in product["zones"][1]["phases"]}
    assert covers["establishment"] == "dry_spell"
    assert covers["flowering"] == "dry_spell"
    assert covers["vegetative"] == "deficit"
    assert covers["grain_filling"] == "deficit"
