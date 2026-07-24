from datetime import date

import numpy as np
import pytest

from app.weather import WeatherStore, days_in_year

BBOX = (33.9, -4.8, 42.0, 5.6)
GRID = {"x0": 33.925, "y0": 5.575, "dx": 0.05, "dy": -0.05, "nx": 162, "ny": 208}


class FakeFetcher:
    """Deterministic stand-in for the CHIRPS download: value = day-of-year,
    except one pixel (row 3, col 7) which is always day-of-year + 100."""

    def __init__(self, missing: set[date] | None = None):
        self.calls = 0
        self.missing = missing or set()

    def __call__(self, day: date, bbox: tuple):
        self.calls += 1
        if day in self.missing:
            return None
        doy = day.timetuple().tm_yday
        arr = np.full((GRID["ny"], GRID["nx"]), float(doy), dtype="float32")
        arr[3, 7] = doy + 100.0
        return arr, dict(GRID)


@pytest.fixture
def store(tmp_path):
    return WeatherStore(cache_dir=tmp_path, fetch_day=FakeFetcher())


def test_ensure_year_downloads_then_caches(tmp_path):
    fetcher = FakeFetcher()
    store = WeatherStore(cache_dir=tmp_path, fetch_day=fetcher)

    meta = store.ensure_year("KEN", 1990, BBOX)
    assert fetcher.calls == 365
    assert meta["dataset"] == "CHIRPS-2.0"
    assert meta["missing_days"] == []
    assert store.cached_years("KEN") == [1990]

    # Cache hit: zero further downloads.
    store.ensure_year("KEN", 1990, BBOX)
    assert fetcher.calls == 365


def test_series_returns_correct_pixel_values(tmp_path):
    store = WeatherStore(cache_dir=tmp_path, fetch_day=FakeFetcher())
    store.ensure_year("KEN", 1990, BBOX)

    # Pixel (row 3, col 7) — its centre coordinates from the grid definition.
    lon = GRID["x0"] + 7 * GRID["dx"]
    lat = GRID["y0"] + 3 * GRID["dy"]
    series = store.series("KEN", lon, lat, date(1990, 2, 1), date(1990, 2, 3))

    assert [p["date"] for p in series] == ["1990-02-01", "1990-02-02", "1990-02-03"]
    assert [p["mm"] for p in series] == [132.0, 133.0, 134.0]  # doy 32..34 + 100

    # Any other pixel gets the plain day-of-year value.
    other = store.series("KEN", lon + 0.5, lat - 0.5, date(1990, 2, 1), date(1990, 2, 1))
    assert other[0]["mm"] == 32.0


def test_missing_day_becomes_nan_and_is_recorded(tmp_path):
    gap = date(1990, 6, 15)
    store = WeatherStore(cache_dir=tmp_path, fetch_day=FakeFetcher(missing={gap}))
    meta = store.ensure_year("KEN", 1990, BBOX)

    assert meta["missing_days"] == ["1990-06-15"]
    series = store.series("KEN", 37.0, 0.0, gap, gap)
    assert series[0]["mm"] is None


def test_series_requires_cache(store):
    with pytest.raises(FileNotFoundError):
        store.series("KEN", 37.0, 0.0, date(1990, 1, 1), date(1990, 1, 2))


def test_out_of_window_coordinates_rejected(tmp_path):
    store = WeatherStore(cache_dir=tmp_path, fetch_day=FakeFetcher())
    store.ensure_year("KEN", 1990, BBOX)
    with pytest.raises(ValueError):
        store.series("KEN", 10.0, 50.0, date(1990, 1, 1), date(1990, 1, 2))


def test_leap_year_has_366_days():
    assert len(days_in_year(1992)) == 366
    assert len(days_in_year(1990)) == 365
