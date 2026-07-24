from datetime import date
from pathlib import Path

from celery.result import AsyncResult
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import settings
from app.countries import COUNTRIES
from app.weather import WeatherStore
from app.worker import celery_app, demo_job, fetch_weather, zoning_run

app = FastAPI(title="AEZ Creator & Weather Index Insurance Platform")


@app.on_event("startup")
def _bootstrap_schema() -> None:
    if settings.database_url:
        from app import crops
        from app.db import init_schema

        try:
            init_schema()
            crops.init_schema()
            crops.seed_if_empty()
        except Exception:
            pass  # /health surfaces db state; don't block startup on a race

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _check_db() -> str:
    """Report database reachability without making it a hard dependency.

    The API must start and answer /health even with no database configured
    (bare local run) so the skeleton is usable before docker compose exists.
    """
    if not settings.database_url:
        return "not_configured"
    try:
        import psycopg

        with psycopg.connect(settings.database_url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return "ok"
    except Exception:
        return "unreachable"


def _check_worker() -> str:
    if not settings.redis_url:
        return "not_configured"
    try:
        replies = celery_app.control.ping(timeout=1.0)
        return "ok" if replies else "no_workers"
    except Exception:
        return "unreachable"


@app.get("/health")
def health() -> dict:
    return {
        "api": "ok",
        "db": _check_db(),
        "worker": _check_worker(),
    }


def _store() -> WeatherStore:
    return WeatherStore(cache_dir=Path(settings.weather_cache_dir))


@app.get("/weather/countries")
def weather_countries() -> list[dict]:
    store = _store()
    return [
        {"code": code, "name": info["name"], "cached_years": store.cached_years(code)}
        for code, info in sorted(COUNTRIES.items(), key=lambda kv: kv[1]["name"])
    ]


class FetchRequest(BaseModel):
    country: str
    start_year: int
    end_year: int


@app.post("/weather/fetch")
def start_weather_fetch(req: FetchRequest) -> dict:
    if req.country not in COUNTRIES:
        raise HTTPException(404, f"Unknown country code: {req.country}")
    current = date.today().year
    if not (1981 <= req.start_year <= req.end_year <= current):
        raise HTTPException(422, f"Years must lie within 1981-{current}")
    task = fetch_weather.delay(req.country, req.start_year, req.end_year)
    return {"job_id": task.id}


@app.get("/weather/series")
def weather_series(country: str, lon: float, lat: float, start: date, end: date) -> dict:
    try:
        series = _store().series(country, lon, lat, start, end)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"country": country, "lon": lon, "lat": lat, "series": series}


class ZoningRequest(BaseModel):
    country: str
    start_year: int
    end_year: int
    n_clusters: int = 15
    sensitivity: float = 1.25
    seed: int = 1
    admin_snap: bool = False


@app.post("/zoning/run")
def start_zoning_run(req: ZoningRequest) -> dict:
    if req.country not in COUNTRIES:
        raise HTTPException(404, f"Unknown country code: {req.country}")
    if not (2 <= req.n_clusters <= 50):
        raise HTTPException(422, "n_clusters must be between 2 and 50")
    years = list(range(req.start_year, req.end_year + 1))
    cached = set(_store().cached_years(req.country))
    missing = [y for y in years if y not in cached]
    if missing:
        raise HTTPException(409, f"Years not cached yet: {missing} — fetch weather data first")
    task = zoning_run.delay(
        req.country, years, req.n_clusters, req.sensitivity, req.seed, req.admin_snap
    )
    return {"job_id": task.id}


def _zoning_dir(country: str) -> Path:
    return Path(settings.weather_cache_dir) / "zoning" / country


@app.get("/zoning/runs")
def list_zoning_runs(country: str) -> list[dict]:
    import json

    d = _zoning_dir(country)
    if not d.exists():
        return []
    runs = []
    for p in sorted(d.glob("*.json"), reverse=True):
        rec = json.loads(p.read_text())
        runs.append(
            {
                "run_id": rec["run_id"],
                "created_at": rec["created_at"],
                "params": rec["params"],
                "homogeneity": rec["homogeneity"],
                "quality_flag": rec.get("quality_flag"),
            }
        )
    return runs


@app.get("/zoning/runs/{country}/{run_id}/geojson")
def zoning_run_geojson(country: str, run_id: str) -> dict:
    import json

    p = _zoning_dir(country) / f"{run_id}.json"
    if not p.exists():
        raise HTTPException(404, f"No zoning run {run_id} for {country}")
    return json.loads(p.read_text())["geojson"]


class ApproveRequest(BaseModel):
    name: str
    approved_by: str


@app.post("/zoning/runs/{country}/{run_id}/approve")
def approve_zone_map(country: str, run_id: str, req: ApproveRequest) -> dict:
    """Freeze a draft zoning run as an immutable, named zone map version."""
    import json

    from app.db import connect

    p = _zoning_dir(country) / f"{run_id}.json"
    if not p.exists():
        raise HTTPException(404, f"No zoning run {run_id} for {country}")
    rec = json.loads(p.read_text())

    try:
        with connect() as conn:
            conn.execute(
                """INSERT INTO zone_map_versions
                   (name, country, run_id, params, homogeneity, geojson, approved_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    req.name,
                    country,
                    run_id,
                    json.dumps(rec["params"]),
                    json.dumps(rec["homogeneity"]),
                    json.dumps(rec["geojson"]),
                    req.approved_by,
                ),
            )
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(409, f"Version name '{req.name}' already exists — versions are immutable")
        raise
    return {"name": req.name, "country": country, "run_id": run_id, "status": "approved"}


@app.get("/zone-maps")
def list_zone_maps(country: str | None = None) -> list[dict]:
    from app.db import connect

    q = """SELECT name, country, run_id, params, homogeneity, approved_by, approved_at
           FROM zone_map_versions"""
    args: tuple = ()
    if country:
        q += " WHERE country = %s"
        args = (country,)
    q += " ORDER BY approved_at DESC"
    with connect() as conn:
        rows = conn.execute(q, args).fetchall()
    return [
        {
            "name": r[0], "country": r[1], "run_id": r[2], "params": r[3],
            "homogeneity": r[4], "approved_by": r[5], "approved_at": r[6].isoformat(),
        }
        for r in rows
    ]


@app.get("/zone-maps/{name}/geojson")
def zone_map_geojson(name: str) -> dict:
    from app.db import connect

    with connect() as conn:
        row = conn.execute(
            "SELECT geojson FROM zone_map_versions WHERE name = %s", (name,)
        ).fetchone()
    if not row:
        raise HTTPException(404, f"No approved zone map named '{name}'")
    return row[0]


@app.get("/crops")
def list_crops() -> list[dict]:
    from app import crops

    return crops.latest_versions()


@app.get("/crops/{crop}/versions")
def crop_versions(crop: str) -> list[dict]:
    from app import crops

    return crops.versions_of(crop)


@app.get("/crops/{crop}/versions/{version}")
def crop_version(crop: str, version: int) -> dict:
    from app import crops

    rec = crops.get_version(crop, version)
    if not rec:
        raise HTTPException(404, f"No version {version} of crop '{crop}'")
    return rec


class CropSaveRequest(BaseModel):
    stages: list[dict]
    seasons: list[dict]
    edited_by: str
    source: str = ""
    reviewed: bool = False


@app.post("/crops/{crop}/versions")
def save_crop_version(crop: str, req: CropSaveRequest) -> dict:
    from app import crops

    warnings = crops.validate(req.stages, req.seasons)
    rec = crops.save_new_version(
        crop, req.stages, req.seasons, req.edited_by, req.source, req.reviewed
    )
    return {**rec, "warnings": warnings}


@app.post("/jobs/demo")
def start_demo_job() -> dict:
    task = demo_job.delay()
    return {"job_id": task.id}


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    result = AsyncResult(job_id, app=celery_app)
    payload: dict = {"job_id": job_id, "state": result.state}
    if result.state == "PROGRESS":
        payload["progress"] = result.info
    elif result.successful():
        payload["result"] = result.result
    return payload
