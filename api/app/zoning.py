"""Zoning Engine — pure computation: cached rainfall in, zones + scores out.

Improvements over the original R script, per SPEC.md:
- features are mean annual rainfall AND inter-annual variability (CV), not just
  a mean total — insurance zones must separate places by how often they fail;
- coordinates are scaled jointly (one shared factor), so the country's shape is
  never silently distorted;
- reproducible by construction: same inputs + seed give identical zones.

No I/O here beyond reading the Weather Store cache; no web, no database.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.weather import WeatherStore


@dataclass
class ZoningResult:
    lons: np.ndarray          # (n_pixels,)
    lats: np.ndarray          # (n_pixels,)
    cluster: np.ndarray       # (n_pixels,) int, 1-based zone ids
    homogeneity: dict[int, float | None]  # zone id -> mean within-zone correlation
    params: dict


def quality_flag(n_years: int) -> str:
    """Data-depth traffic light: advisory only, never blocking."""
    return "green" if n_years >= 15 else "amber" if n_years >= 10 else "red"


def pixel_features(store: WeatherStore, country: str, years: list[int]):
    """Per-pixel features across years: lon, lat, mean annual total, CV.

    Returns (lons, lats, mean_total, cv, annual_totals) for pixels that have
    data in every requested year; annual_totals is (n_years, n_pixels) for
    homogeneity scoring later.
    """
    annual = []
    grid = None
    for year in years:
        meta = store.meta(country, year)
        grid = meta["grid"]
        stack = np.load(store._year_path(country, year))["precip"]
        annual.append(np.nansum(stack, axis=0))
    totals = np.stack(annual)  # (n_years, ny, nx)

    # A pixel with no data at all in some year (all-NaN => nansum 0 over ocean
    # mask) is excluded via the NaN mask of any single day.
    sample_year = years[0]
    first_day = np.load(store._year_path(country, sample_year))["precip"][0]
    valid = ~np.isnan(first_day)

    ny, nx = first_day.shape
    lon_axis = grid["x0"] + np.arange(nx) * grid["dx"]
    lat_axis = grid["y0"] + np.arange(ny) * grid["dy"]
    lon_mesh, lat_mesh = np.meshgrid(lon_axis, lat_axis)

    lons = lon_mesh[valid]
    lats = lat_mesh[valid]
    totals_v = totals[:, valid]  # (n_years, n_pixels)
    mean_total = totals_v.mean(axis=0)
    std_total = totals_v.std(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        cv = np.where(mean_total > 0, std_total / mean_total, 0.0)
    return lons, lats, mean_total, cv, totals_v


def run_zoning(
    store: WeatherStore,
    country: str,
    years: list[int],
    n_clusters: int,
    sensitivity: float = 1.25,
    seed: int = 1,
) -> ZoningResult:
    from sklearn.cluster import KMeans

    lons, lats, mean_total, cv, totals_v = pixel_features(store, country, years)

    # Joint coordinate scaling: one shared factor for lon and lat, so distances
    # on the ground keep their aspect ratio in feature space.
    lon_c, lat_c = lons - lons.mean(), lats - lats.mean()
    coord_scale = np.sqrt((lon_c**2 + lat_c**2).mean()) or 1.0

    def z(x: np.ndarray) -> np.ndarray:
        s = x.std()
        return (x - x.mean()) / (s if s > 0 else 1.0)

    features = np.column_stack(
        [
            lon_c / coord_scale,
            lat_c / coord_scale,
            z(mean_total) * sensitivity,
            z(cv) * sensitivity,
        ]
    )

    km = KMeans(n_clusters=n_clusters, n_init=10, max_iter=300, random_state=seed)
    labels = km.fit_predict(features) + 1  # 1-based zone ids

    homogeneity = _homogeneity(labels, totals_v)
    return ZoningResult(
        lons=lons,
        lats=lats,
        cluster=labels,
        homogeneity=homogeneity,
        params={
            "country": country,
            "years": years,
            "n_clusters": n_clusters,
            "sensitivity": sensitivity,
            "seed": seed,
        },
    )


def zones_geojson(result: ZoningResult, dx: float, dy: float) -> dict:
    """Dissolve pixel cells into one (Multi)Polygon per zone -> GeoJSON."""
    from shapely.geometry import box, mapping
    from shapely.ops import unary_union

    features = []
    for zone in np.unique(result.cluster):
        mask = result.cluster == zone
        cells = [
            box(lon - dx / 2, lat - abs(dy) / 2, lon + dx / 2, lat + abs(dy) / 2)
            for lon, lat in zip(result.lons[mask], result.lats[mask])
        ]
        geom = unary_union(cells)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "zone": int(zone),
                    "pixels": int(mask.sum()),
                    "homogeneity": result.homogeneity.get(int(zone)),
                },
                "geometry": mapping(geom),
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _homogeneity(labels: np.ndarray, totals_v: np.ndarray) -> dict[int, float | None]:
    """Mean correlation between each pixel's annual-total series and its
    zone's mean series. This IS the zone's basis-risk promise, quantified.
    Needs >= 3 years to say anything; returns None per zone below that."""
    n_years = totals_v.shape[0]
    scores: dict[int, float | None] = {}
    for zone in np.unique(labels):
        members = totals_v[:, labels == zone]  # (n_years, n_members)
        if n_years < 3 or members.shape[1] < 2:
            scores[int(zone)] = None
            continue
        zone_mean = members.mean(axis=1)
        zm = zone_mean - zone_mean.mean()
        mm = members - members.mean(axis=0)
        denom = np.sqrt((zm**2).sum()) * np.sqrt((mm**2).sum(axis=0))
        with np.errstate(divide="ignore", invalid="ignore"):
            corr = np.where(denom > 0, (mm * zm[:, None]).sum(axis=0) / denom, np.nan)
        if np.all(np.isnan(corr)):
            scores[int(zone)] = None
        else:
            scores[int(zone)] = float(np.clip(np.nanmean(corr), -1.0, 1.0))
    return scores
