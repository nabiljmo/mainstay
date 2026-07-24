"""GADM administrative boundaries: fetched from the official UC Davis source
on first use, cached like weather data. Level 2 = districts/sub-counties."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

GADM_URL = "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_{iso}_{level}.json.zip"


def fetch_gadm(cache_dir: Path, iso: str, level: int = 2) -> dict:
    """GeoJSON FeatureCollection of admin boundaries, cached on disk."""
    cache = Path(cache_dir) / "gadm" / f"{iso}_{level}.json"
    if cache.exists():
        return json.loads(cache.read_text())

    import requests

    resp = requests.get(GADM_URL.format(iso=iso, level=level), timeout=300)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        raw = zf.read(zf.namelist()[0])
    geojson = json.loads(raw)

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(geojson))
    return geojson


def snap_to_admin(
    lons, lats, cluster, districts: dict
) -> tuple[dict, "object"]:
    """Assign each district wholly to the zone covering most of its pixels.

    Returns (district-aligned FeatureCollection, per-pixel snapped labels)
    where snapped labels are None for pixels outside every district — which
    also clips the zone map to the country outline.
    """
    import numpy as np
    from shapely import STRtree, points
    from shapely.geometry import shape

    pixel_points = points(np.column_stack([lons, lats]))
    tree = STRtree(pixel_points)

    snapped = np.full(len(lons), 0, dtype=int)  # 0 = outside all districts
    features = []
    for feat in districts["features"]:
        geom = shape(feat["geometry"])
        idx = tree.query(geom, predicate="covers")
        name = feat["properties"].get("NAME_2") or feat["properties"].get("NAME_1", "?")
        if len(idx) == 0:
            zone = None
        else:
            zone = int(np.bincount(cluster[idx]).argmax())
            snapped[idx] = zone
        features.append(
            {
                "type": "Feature",
                "properties": {"district": name, "zone": zone, "pixels": int(len(idx))},
                "geometry": feat["geometry"],
            }
        )
    return {"type": "FeatureCollection", "features": features}, snapped
