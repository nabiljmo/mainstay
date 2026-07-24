import time

from celery import Celery

from app.config import settings

celery_app = Celery(
    "aez",
    broker=settings.redis_url or "redis://localhost:6379/0",
    backend=settings.redis_url or "redis://localhost:6379/0",
)
celery_app.conf.task_track_started = True


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
def demo_job(self, steps: int = 5) -> dict:
    """Walking-skeleton job: proves the api -> broker -> worker -> result
    round-trip that every real job (fetching, zoning, pricing) will use."""
    for i in range(steps):
        time.sleep(1)
        self.update_state(state="PROGRESS", meta={"done": i + 1, "total": steps})
    return {"done": steps, "total": steps}
