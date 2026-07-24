from datetime import date

import numpy as np
import pytest

from app.weather import WeatherStore
from app.zoning import run_zoning

# Small synthetic country: 20x30 pixels, western half wet, eastern half dry.
GRID = {"x0": 34.0, "y0": 2.0, "dx": 0.1, "dy": -0.1, "nx": 30, "ny": 20}
BBOX = (34.0, 0.0, 37.0, 2.0)


class TwoRegimeFetcher:
    """West half: wet (5 mm/day) and volatile between years.
    East half: dry (1 mm/day) and stable. A clean two-zone country."""

    def __call__(self, day: date, bbox: tuple):
        arr = np.full((GRID["ny"], GRID["nx"]), 1.0, dtype="float32")
        year_factor = 1.0 + 0.5 * ((day.year % 3) - 1)  # varies 0.5 / 1.0 / 1.5
        arr[:, : GRID["nx"] // 2] = 5.0 * year_factor
        return arr, dict(GRID)


@pytest.fixture
def cached_store(tmp_path):
    store = WeatherStore(cache_dir=tmp_path, fetch_day=TwoRegimeFetcher())
    for year in (1990, 1991, 1992, 1993):
        store.ensure_year("TST", year, BBOX)
    return store


def test_zoning_is_reproducible(cached_store):
    years = [1990, 1991, 1992, 1993]
    a = run_zoning(cached_store, "TST", years, n_clusters=2, seed=1)
    b = run_zoning(cached_store, "TST", years, n_clusters=2, seed=1)
    assert np.array_equal(a.cluster, b.cluster)


def test_two_regimes_separate_cleanly(cached_store):
    result = run_zoning(cached_store, "TST", [1990, 1991, 1992, 1993], n_clusters=2)
    west = result.cluster[result.lons < 35.5]
    east = result.cluster[result.lons >= 35.5]
    # Each side should be essentially one zone, and different from the other.
    west_zone = np.bincount(west).argmax()
    east_zone = np.bincount(east).argmax()
    assert west_zone != east_zone
    assert (west == west_zone).mean() > 0.95
    assert (east == east_zone).mean() > 0.95


def test_homogeneity_scores_are_valid(cached_store):
    result = run_zoning(cached_store, "TST", [1990, 1991, 1992, 1993], n_clusters=2)
    assert set(result.homogeneity) == set(np.unique(result.cluster).tolist())
    for score in result.homogeneity.values():
        assert score is None or -1.0 <= score <= 1.0


def test_homogeneity_needs_three_years(tmp_path):
    store = WeatherStore(cache_dir=tmp_path, fetch_day=TwoRegimeFetcher())
    for year in (1990, 1991):
        store.ensure_year("TST", year, BBOX)
    result = run_zoning(store, "TST", [1990, 1991], n_clusters=2)
    assert all(v is None for v in result.homogeneity.values())


def test_zones_geojson_one_feature_per_zone(cached_store):
    from app.zoning import zones_geojson

    result = run_zoning(cached_store, "TST", [1990, 1991, 1992, 1993], n_clusters=2)
    fc = zones_geojson(result, GRID["dx"], GRID["dy"])
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 2
    zones = {f["properties"]["zone"] for f in fc["features"]}
    assert zones == set(np.unique(result.cluster).tolist())
    total_pixels = sum(f["properties"]["pixels"] for f in fc["features"])
    assert total_pixels == len(result.cluster)


def test_engine_agrees_with_r_script_method(cached_store):
    """Cross-check replacing the lost Nigeria benchmark: replicate the R
    script's exact method (k-means on independently-scaled x, y, mean total —
    R's scale() + kmeans) and confirm both methods recover the same partition
    of a country with clear climate regimes."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import adjusted_rand_score

    from app.zoning import pixel_features

    years = [1990, 1991, 1992, 1993]
    lons, lats, mean_total, _cv, _ = pixel_features(cached_store, "TST", years)

    # The R script: scale() standardises each column independently,
    # then layer (rainfall) is multiplied by R_Sensitivity.
    def r_scale(x):
        return (x - x.mean()) / x.std(ddof=1)  # R uses sample sd

    r_features = np.column_stack(
        [r_scale(lons), r_scale(lats), r_scale(mean_total) * 1.25]
    )
    r_labels = KMeans(n_clusters=2, n_init=50, max_iter=20, random_state=1).fit_predict(
        r_features
    )

    ours = run_zoning(cached_store, "TST", years, n_clusters=2)
    # Same partition up to label naming: adjusted Rand index of 1.0.
    assert adjusted_rand_score(r_labels, ours.cluster) == 1.0


def test_quality_flag_boundaries():
    from app.zoning import quality_flag

    assert quality_flag(5) == "red"
    assert quality_flag(9) == "red"
    assert quality_flag(10) == "amber"
    assert quality_flag(14) == "amber"
    assert quality_flag(15) == "green"
    assert quality_flag(30) == "green"


def test_params_recorded(cached_store):
    result = run_zoning(
        cached_store, "TST", [1990, 1991, 1992, 1993], n_clusters=2, sensitivity=2.0, seed=7
    )
    assert result.params["sensitivity"] == 2.0
    assert result.params["seed"] == 7
