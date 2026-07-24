import numpy as np

from app.admin_boundaries import snap_to_admin

# A 10x10 pixel grid from (0,0) to (9,9); two rectangular "districts":
# west covers x in [-0.5, 4.5], east covers x in [4.5, 8.5].
# Pixels at x=9 fall outside every district.
WEST = {
    "type": "Feature",
    "properties": {"NAME_2": "West District"},
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[-0.5, -0.5], [4.5, -0.5], [4.5, 9.5], [-0.5, 9.5], [-0.5, -0.5]]],
    },
}
EAST = {
    "type": "Feature",
    "properties": {"NAME_2": "East District"},
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[4.5, -0.5], [8.5, -0.5], [8.5, 9.5], [4.5, 9.5], [4.5, -0.5]]],
    },
}
DISTRICTS = {"type": "FeatureCollection", "features": [WEST, EAST]}


def _pixels():
    xs, ys = np.meshgrid(np.arange(10.0), np.arange(10.0))
    return xs.ravel(), ys.ravel()


def test_majority_assignment_and_clipping():
    lons, lats = _pixels()
    # Mostly zone 1 in the west, zone 2 in the east — with noise: one eastern
    # column (x=5) votes zone 1, but the east majority is still zone 2.
    cluster = np.where(lons <= 5, 1, 2)

    geojson, snapped = snap_to_admin(lons, lats, cluster, DISTRICTS)

    by_name = {f["properties"]["district"]: f["properties"] for f in geojson["features"]}
    assert by_name["West District"]["zone"] == 1
    assert by_name["East District"]["zone"] == 2

    # Every pixel in a district carries its district's zone (noise overridden).
    assert set(snapped[(lons > 5) & (lons <= 8)]) == {2}
    assert set(snapped[lons == 5]) == {2}  # x=5 sits in East despite voting 1

    # Pixels outside all districts are clipped out (label 0).
    assert set(snapped[lons == 9]) == {0}


def test_district_without_pixels_gets_no_zone():
    lons, lats = _pixels()
    cluster = np.ones(len(lons), dtype=int)
    far_away = {
        "type": "Feature",
        "properties": {"NAME_2": "Empty District"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[100, 100], [101, 100], [101, 101], [100, 101], [100, 100]]],
        },
    }
    districts = {"type": "FeatureCollection", "features": [far_away]}
    geojson, snapped = snap_to_admin(lons, lats, cluster, districts)
    assert geojson["features"][0]["properties"]["zone"] is None
    assert set(snapped) == {0}
