import time

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "aez",
    broker=settings.redis_url or "redis://localhost:6379/0",
    backend=settings.redis_url or "redis://localhost:6379/0",
)
celery_app.conf.task_track_started = True

# The platform's first scheduled job (SPEC §9): each day, check for newly
# published CHIRPS final data and settle any phase that has just gone final.
# Runs under `celery beat`; nothing else in the system needs a manual trigger.
celery_app.conf.beat_schedule = {
    "settle-due-daily": {
        "task": "app.worker.settle_due",
        "schedule": crontab(hour=3, minute=0),
    },
}


@celery_app.task(bind=True)
def fetch_weather(self, country: str, start_year: int, end_year: int) -> dict:
    """Fetch and cache CHIRPS for a country across a year range, with progress."""
    from pathlib import Path

    from app.countries import COUNTRIES
    from app.weather import WeatherStore

    store = WeatherStore(cache_dir=Path(settings.weather_cache_dir))
    bbox = tuple(COUNTRIES[country]["bbox"])
    years = list(range(start_year, end_year + 1))
    fetched, missing_days = [], 0

    for n, year in enumerate(years):
        def day_progress(done: int, total: int) -> None:
            self.update_state(
                state="PROGRESS",
                meta={
                    "year": year, "day": done, "days_total": total,
                    "years_done": n, "years_total": len(years),
                },
            )

        meta = store.ensure_year(country, year, bbox, progress=day_progress)
        fetched.append(year)
        missing_days += len(meta["missing_days"])

    return {"country": country, "years": fetched, "missing_days": missing_days}


@celery_app.task(bind=True)
def zoning_run(
    self,
    country: str,
    years: list[int],
    n_clusters: int,
    sensitivity: float,
    seed: int,
    admin_snap: bool = False,
) -> dict:
    """Run the Zoning Engine and store the draft run (zones + scores + GeoJSON)."""
    import json
    from datetime import datetime
    from pathlib import Path

    from app.weather import WeatherStore
    from app.zoning import run_zoning, zones_geojson

    store = WeatherStore(cache_dir=Path(settings.weather_cache_dir))

    self.update_state(state="PROGRESS", meta={"stage": "clustering"})
    result = run_zoning(store, country, years, n_clusters, sensitivity, seed)

    if admin_snap:
        import numpy as np

        from app.admin_boundaries import fetch_gadm, snap_to_admin
        from app.zoning import _homogeneity, pixel_features

        self.update_state(state="PROGRESS", meta={"stage": "aligning to districts"})
        districts = fetch_gadm(Path(settings.weather_cache_dir), country, level=2)
        geojson, snapped = snap_to_admin(result.lons, result.lats, result.cluster, districts)
        # Homogeneity for the snapped zones, on pixels inside the country only.
        inside = snapped > 0
        _, _, _, _, totals_v = pixel_features(store, country, years)
        homogeneity = _homogeneity(snapped[inside], totals_v[:, inside])
        for feat in geojson["features"]:
            zone = feat["properties"]["zone"]
            feat["properties"]["homogeneity"] = homogeneity.get(zone) if zone else None
    else:
        self.update_state(state="PROGRESS", meta={"stage": "building polygons"})
        grid = store.meta(country, years[0])["grid"]
        geojson = zones_geojson(result, grid["dx"], grid["dy"])
        homogeneity = result.homogeneity

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(settings.weather_cache_dir) / "zoning" / country
    run_dir.mkdir(parents=True, exist_ok=True)
    from app.zoning import quality_flag

    quality = quality_flag(len(years))
    params = dict(result.params, admin_snap=admin_snap)
    record = {
        "quality_flag": quality,
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "params": params,
        "homogeneity": {str(k): v for k, v in homogeneity.items()},
        "geojson": geojson,
    }
    (run_dir / f"{run_id}.json").write_text(json.dumps(record))
    return {"run_id": run_id, "country": country, "zones": n_clusters}


@celery_app.task(bind=True)
def draft_product(
    self,
    country: str,
    zone_map: str,
    crop: str,
    crop_version: int,
    season: str,
    years: list[int],
    sum_insured: float,
    created_by: str,
) -> dict:
    """Assemble a draft product: propose phases/triggers per zone from history."""
    import json
    from datetime import datetime
    from pathlib import Path

    from app import crops
    from app.db import connect
    from app.products import propose_product
    from app.weather import WeatherStore

    self.update_state(state="PROGRESS", meta={"stage": "loading zone map + crop"})
    with connect() as conn:
        row = conn.execute(
            "SELECT geojson FROM zone_map_versions WHERE name = %s", (zone_map,)
        ).fetchone()
    if not row:
        raise ValueError(f"No approved zone map named {zone_map}")
    zone_geojson = row[0]

    crop_rec = crops.get_version(crop, crop_version)
    if not crop_rec:
        raise ValueError(f"No crop {crop} v{crop_version}")
    season_rec = next(
        (s for s in crop_rec["seasons"] if s["country"] == country and s["season"] == season),
        None,
    )
    if not season_rec:
        raise ValueError(f"Crop {crop} has no {season} window for {country}")

    self.update_state(state="PROGRESS", meta={"stage": "computing indices per zone"})
    store = WeatherStore(cache_dir=Path(settings.weather_cache_dir))
    definition = propose_product(
        store, country, years, zone_geojson,
        crop_rec["stages"], season_rec["plant_start"], sum_insured,
    )

    draft_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    with connect() as conn:
        conn.execute(
            """INSERT INTO product_drafts
               (id, country, zone_map, crop, crop_version, season, years,
                sum_insured, definition, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                draft_id, country, zone_map, crop, crop_version, season,
                json.dumps(years), sum_insured, json.dumps(definition), created_by,
            ),
        )
    return {"draft_id": draft_id, "zones": len(definition["zones"])}


@celery_app.task(bind=True)
def settle_due(self, product_id: str | None = None, season_year: int | None = None) -> dict:
    """Scheduled settlement sweep: refresh the live season's CHIRPS, compute each
    product's newly-final phases, and persist them. Provisional never persists."""
    from app.settlement import run_settlement_sweep

    self.update_state(state="PROGRESS", meta={"stage": "settling due phases"})
    return run_settlement_sweep(product_id=product_id, season_year=season_year)


@celery_app.task(bind=True)
def demo_job(self, steps: int = 5) -> dict:
    """Walking-skeleton job: proves the api -> broker -> worker -> result
    round-trip that every real job (fetching, zoning, pricing) will use."""
    for i in range(steps):
        time.sleep(1)
        self.update_state(state="PROGRESS", meta={"done": i + 1, "total": steps})
    return {"done": steps, "total": steps}
