"""Product assembly — bind an approved zone map + a crop version into a
draftable product, and compute its historical index/payout per zone.

This is the glue between the pure Index Engine and the cached CHIRPS data:
it extracts each zone's area-average daily rainfall (the settlement index every
farmer in the zone shares) and runs the engine over each historical season.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.index_engine import (
    apportion_limits,
    default_cover_for,
    phase_from_dict,
    phase_index,
    phase_windows,
    planting_day_of_year,
    propose_triggers,
    run_year,
)
from app.weather import WeatherStore, days_in_year


def zone_daily_series(
    store: WeatherStore, country: str, year: int, zone_geojson: dict
) -> dict[int, np.ndarray]:
    """Area-average daily rainfall for each zone in a year.

    Returns {zone_id: daily_mm array}. The zone's index is the mean across the
    pixels whose centres fall in the zone polygon — the classic area-average
    settlement index.
    """
    from shapely import STRtree, points
    from shapely.geometry import shape

    meta = store.meta(country, year)
    grid = meta["grid"]
    stack = np.load(store._year_path(country, year))["precip"]  # (days, ny, nx)
    n_days = stack.shape[0]

    nx, ny = grid["nx"], grid["ny"]
    lon_axis = grid["x0"] + np.arange(nx) * grid["dx"]
    lat_axis = grid["y0"] + np.arange(ny) * grid["dy"]
    lon_mesh, lat_mesh = np.meshgrid(lon_axis, lat_axis)
    flat_pts = points(np.column_stack([lon_mesh.ravel(), lat_mesh.ravel()]))
    tree = STRtree(flat_pts)

    series: dict[int, np.ndarray] = {}
    for feat in zone_geojson["features"]:
        zone = feat["properties"].get("zone")
        if zone is None:
            continue
        geom = shape(feat["geometry"])
        idx = tree.query(geom, predicate="covers")
        if len(idx) == 0:
            continue
        rows, cols = np.unravel_index(idx, (ny, nx))
        # mean across the zone's pixels, per day (ignoring NaN pixels)
        zone_stack = stack[:, rows, cols]           # (days, n_pixels)
        series[int(zone)] = np.nanmean(zone_stack, axis=1)
    return series


def propose_product(
    store: WeatherStore,
    country: str,
    years: list[int],
    zone_geojson: dict,
    stages: list[dict],
    plant_start: str,
    sum_insured: float,
) -> dict:
    """Build a draft product: propose phases, cover types, and percentile
    triggers per zone from the historical index distribution.

    Returns {zone_id: {phases: [Phase-as-dict], ...}} plus the shared phase
    layout. Everything here is a *proposal* the actuary can override.
    """
    windows = phase_windows(stages)
    limits = apportion_limits(stages, sum_insured)

    # Gather each zone's per-year daily series once.
    per_year_series: dict[int, dict[int, np.ndarray]] = {}
    for year in years:
        per_year_series[year] = zone_daily_series(store, country, year, zone_geojson)

    zones = sorted(
        {z for y in per_year_series.values() for z in y}
    )

    result_zones = {}
    for zone in zones:
        phases = []
        for (name, start, end, _sens), limit in zip(windows, limits):
            cover = default_cover_for(name)
            # historical index values for this phase across years
            history = []
            for year in years:
                series = per_year_series[year].get(zone)
                if series is None:
                    continue
                plant_idx = planting_day_of_year(plant_start, year)
                sl = series[plant_idx + start : plant_idx + end]
                history.append(phase_index(sl, cover))
            strike, exit_ = propose_triggers(history, cover)
            reference = float(np.mean(history)) if history else 0.0
            # Express the same triggers as a % of normal so the actuary can
            # work in either unit. Percent is the default when a normal exists.
            if reference:
                strike_pct = round(100 * strike / reference, 1)
                exit_pct = round(100 * exit_ / reference, 1)
                trigger_mode = "percent"
            else:
                strike_pct = exit_pct = None
                trigger_mode = "absolute"
            phases.append(
                {
                    "name": name,
                    "cover_type": cover,
                    "start_offset": start,
                    "end_offset": end,
                    "strike": strike,
                    "exit": exit_,
                    "reference": round(reference, 2),
                    "strike_pct": strike_pct,
                    "exit_pct": exit_pct,
                    "trigger_mode": trigger_mode,
                    "limit": round(limit, 2),
                    "history": [round(h, 2) for h in history],
                }
            )
        result_zones[zone] = {"phases": phases}

    return {
        "country": country,
        "years": years,
        "sum_insured": sum_insured,
        "plant_start": plant_start,
        "phase_layout": [{"name": n, "start": s, "end": e} for (n, s, e, _) in windows],
        "zones": result_zones,
    }


def historical_table(
    store: WeatherStore,
    country: str,
    years: list[int],
    zone_geojson: dict,
    zone_id: int,
    phases_def: list[dict],
    plant_start: str,
) -> list[dict]:
    """Per-year index and payout for one zone, given finalised phase terms."""
    phases = [phase_from_dict(p) for p in phases_def]
    rows = []
    for year in years:
        series = zone_daily_series(store, country, year, zone_geojson).get(zone_id)
        if series is None:
            continue
        plant_idx = planting_day_of_year(plant_start, year)
        year_result = run_year(series, plant_idx, phases)
        total_payout = sum(r["payout"] for r in year_result)
        rows.append({"year": year, "phases": year_result, "total_payout": round(total_payout, 2)})
    return rows
