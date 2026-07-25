from datetime import date
from pathlib import Path

from celery.result import AsyncResult
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import settings
from app.countries import COUNTRIES
from app.weather import WeatherStore
from app.worker import celery_app, demo_job, draft_product, fetch_weather, zoning_run

app = FastAPI(title="Mainstay — Weather Index Insurance Platform")


@app.on_event("startup")
def _bootstrap_schema() -> None:
    if settings.database_url:
        from app import auth, crops, payout, policies, publish, quotes, settlement
        from app.db import init_schema

        try:
            init_schema()
            crops.init_schema()
            crops.seed_if_empty()
            publish.init_schema()
            quotes.init_schema()
            policies.init_schema()
            settlement.init_schema()
            payout.init_schema()
            auth.init_schema()
            auth.seed_admin()
        except Exception:
            pass  # /health surfaces db state; don't block startup on a race

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,  # session cookie rides cross-origin (same-site localhost)
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


# ---------------------------------------------------------------------------
# Auth (issue 013): login with per-user accounts; role checks live in app.auth.
# ---------------------------------------------------------------------------

from fastapi import Cookie, Depends, Response  # noqa: E402

from app.auth import COOKIE_NAME, SESSION_HOURS, current_user, require  # noqa: E402

# Reusable role gates (admin passes all of them). INTERNAL = staff workbench
# (everyone except field agents); AUTHED = any logged-in user.
AUTHED = require()
INTERNAL = require("actuary", "agronomist", "operations")
ACTUARY = require("actuary")
AGRONOMIST = require("agronomist")
OPERATIONS = require("operations")
AGENT = require("agent")


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/login")
def auth_login(req: LoginRequest, response: Response) -> dict:
    from app.auth import login, user_for_token

    token = login(req.username, req.password)
    if not token:
        raise HTTPException(401, "Invalid username or password")
    response.set_cookie(
        key=COOKIE_NAME, value=token, httponly=True, samesite="lax",
        max_age=SESSION_HOURS * 3600, path="/",
    )
    return user_for_token(token)


@app.post("/auth/logout")
def auth_logout(response: Response, aez_session: str | None = Cookie(default=None)) -> dict:
    from app.auth import logout

    logout(aez_session)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/auth/me")
def auth_me(user: dict = Depends(current_user)) -> dict:
    return user


# ----- admin: user management (admin only) -----

class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str


class UpdateUserRequest(BaseModel):
    active: bool | None = None
    role: str | None = None
    password: str | None = None


@app.get("/admin/users")
def admin_list_users(_: dict = Depends(require("admin"))) -> list[dict]:
    from app.auth import list_users

    return list_users()


@app.post("/admin/users")
def admin_create_user(req: CreateUserRequest, user: dict = Depends(require("admin"))) -> dict:
    from app.auth import create_user

    try:
        return create_user(req.username, req.password, req.role, created_by=user["username"])
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.patch("/admin/users/{username}")
def admin_update_user(username: str, req: UpdateUserRequest,
                      _: dict = Depends(require("admin"))) -> dict:
    from app.auth import list_users, set_active, set_password, set_role

    if not any(u["username"] == username for u in list_users()):
        raise HTTPException(404, f"No user {username}")
    try:
        if req.role is not None:
            set_role(username, req.role)
        if req.active is not None:
            set_active(username, req.active)
        if req.password:
            set_password(username, req.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return next(u for u in list_users() if u["username"] == username)


def _store() -> WeatherStore:
    return WeatherStore(cache_dir=Path(settings.weather_cache_dir))


@app.get("/weather/countries")
def weather_countries(_: dict = Depends(INTERNAL)) -> list[dict]:
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
def start_weather_fetch(req: FetchRequest, _: dict = Depends(ACTUARY)) -> dict:
    if req.country not in COUNTRIES:
        raise HTTPException(404, f"Unknown country code: {req.country}")
    current = date.today().year
    if not (1981 <= req.start_year <= req.end_year <= current):
        raise HTTPException(422, f"Years must lie within 1981-{current}")
    task = fetch_weather.delay(req.country, req.start_year, req.end_year)
    return {"job_id": task.id}


@app.get("/weather/series")
def weather_series(country: str, lon: float, lat: float, start: date, end: date,
                   _: dict = Depends(INTERNAL)) -> dict:
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
def start_zoning_run(req: ZoningRequest, _: dict = Depends(ACTUARY)) -> dict:
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
def list_zoning_runs(country: str, _: dict = Depends(INTERNAL)) -> list[dict]:
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
def zoning_run_geojson(country: str, run_id: str, _: dict = Depends(INTERNAL)) -> dict:
    import json

    p = _zoning_dir(country) / f"{run_id}.json"
    if not p.exists():
        raise HTTPException(404, f"No zoning run {run_id} for {country}")
    return json.loads(p.read_text())["geojson"]


class ApproveRequest(BaseModel):
    name: str


@app.post("/zoning/runs/{country}/{run_id}/approve")
def approve_zone_map(country: str, run_id: str, req: ApproveRequest,
                     user: dict = Depends(ACTUARY)) -> dict:
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
                    user["username"],
                ),
            )
    except Exception as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(409, f"Version name '{req.name}' already exists — versions are immutable")
        raise
    return {"name": req.name, "country": country, "run_id": run_id, "status": "approved"}


@app.get("/zone-maps")
def list_zone_maps(country: str | None = None, _: dict = Depends(INTERNAL)) -> list[dict]:
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
def zone_map_geojson(name: str, _: dict = Depends(INTERNAL)) -> dict:
    from app.db import connect

    with connect() as conn:
        row = conn.execute(
            "SELECT geojson FROM zone_map_versions WHERE name = %s", (name,)
        ).fetchone()
    if not row:
        raise HTTPException(404, f"No approved zone map named '{name}'")
    return row[0]


@app.get("/crops")
def list_crops(_: dict = Depends(INTERNAL)) -> list[dict]:
    from app import crops

    return crops.latest_versions()


@app.get("/crops/{crop}/versions")
def crop_versions(crop: str, _: dict = Depends(INTERNAL)) -> list[dict]:
    from app import crops

    return crops.versions_of(crop)


@app.get("/crops/{crop}/versions/{version}")
def crop_version(crop: str, version: int, _: dict = Depends(INTERNAL)) -> dict:
    from app import crops

    rec = crops.get_version(crop, version)
    if not rec:
        raise HTTPException(404, f"No version {version} of crop '{crop}'")
    return rec


class CropSaveRequest(BaseModel):
    stages: list[dict]
    seasons: list[dict]
    source: str = ""
    reviewed: bool = False


@app.post("/crops/{crop}/versions")
def save_crop_version(crop: str, req: CropSaveRequest,
                      user: dict = Depends(AGRONOMIST)) -> dict:
    from app import crops

    warnings = crops.validate(req.stages, req.seasons)
    rec = crops.save_new_version(
        crop, req.stages, req.seasons, user["username"], req.source, req.reviewed
    )
    return {**rec, "warnings": warnings}


class ProductDraftRequest(BaseModel):
    country: str
    zone_map: str
    crop: str
    crop_version: int
    season: str
    start_year: int
    end_year: int
    sum_insured: float = 10000.0


@app.post("/products/draft")
def start_product_draft(req: ProductDraftRequest, user: dict = Depends(ACTUARY)) -> dict:
    years = list(range(req.start_year, req.end_year + 1))
    cached = set(_store().cached_years(req.country))
    missing = [y for y in years if y not in cached]
    if missing:
        raise HTTPException(409, f"Years not cached: {missing} — fetch weather data first")
    task = draft_product.delay(
        req.country, req.zone_map, req.crop, req.crop_version, req.season,
        years, req.sum_insured, user["username"],
    )
    return {"job_id": task.id}


@app.get("/products/drafts")
def list_product_drafts(_: dict = Depends(ACTUARY)) -> list[dict]:
    from app.db import connect

    with connect() as conn:
        rows = conn.execute(
            """SELECT id, country, zone_map, crop, crop_version, season, years,
                      sum_insured, created_by, created_at
               FROM product_drafts ORDER BY created_at DESC"""
        ).fetchall()
    return [
        {
            "id": r[0], "country": r[1], "zone_map": r[2], "crop": r[3],
            "crop_version": r[4], "season": r[5], "years": r[6],
            "sum_insured": r[7], "created_by": r[8], "created_at": r[9].isoformat(),
        }
        for r in rows
    ]


@app.get("/products/drafts/{draft_id}")
def get_product_draft(draft_id: str, _: dict = Depends(ACTUARY)) -> dict:
    from app.db import connect

    with connect() as conn:
        row = conn.execute(
            "SELECT definition FROM product_drafts WHERE id = %s", (draft_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, f"No product draft {draft_id}")
    return row[0]


class PriceZoneRequest(BaseModel):
    phases: list[dict]


@app.post("/products/drafts/{draft_id}/zones/{zone}/price")
def price_zone(draft_id: str, zone: int, req: PriceZoneRequest,
               _: dict = Depends(ACTUARY)) -> dict:
    """Recompute the per-year index/payout table for one zone with the given
    (possibly edited) phase terms — the actuary's live feedback loop."""
    from app.db import connect
    from app.products import historical_table

    with connect() as conn:
        row = conn.execute(
            """SELECT country, zone_map, years, definition
               FROM product_drafts WHERE id = %s""",
            (draft_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, f"No product draft {draft_id}")
    country, zone_map, years, definition = row

    with connect() as conn:
        zrow = conn.execute(
            "SELECT geojson FROM zone_map_versions WHERE name = %s", (zone_map,)
        ).fetchone()
    zone_geojson = zrow[0]

    table = historical_table(
        _store(), country, years, zone_geojson, zone, req.phases, definition["plant_start"]
    )
    sum_insured = definition["sum_insured"]
    avg_payout = sum(r["total_payout"] for r in table) / len(table) if table else 0.0

    # Auto-explainer: attach plain-words explanations to every calculation.
    from app.explain import (
        explain_burning_cost,
        explain_payout,
        explain_phase,
        explain_year,
    )
    from app.index_engine import phase_from_dict

    resolved = {}
    phase_meanings = []
    for p in req.phases:
        rp = phase_from_dict({**p, "trigger_mode": p.get("trigger_mode", "absolute")})
        resolved[rp.name] = (rp, p.get("reference"))
        phase_meanings.append(
            {
                "name": rp.name,
                "meaning": explain_phase(rp.name, rp.cover_type, p.get("reference"), rp.strike, rp.exit_, rp.limit),
            }
        )

    for yr in table:
        for ph in yr["phases"]:
            rp, _ref = resolved[ph["phase"]]
            ph["why"] = explain_payout(
                yr["year"], rp.name, rp.cover_type, ph["index"], rp.strike, rp.exit_, ph["limit"], ph["payout"]
            )
        yr["summary"] = explain_year(yr["year"], yr["phases"], sum_insured)

    return {
        "zone": zone,
        "years": table,
        "burning_cost": round(avg_payout, 2),
        "sum_insured": sum_insured,
        "phase_meanings": phase_meanings,
        "burning_cost_explanation": explain_burning_cost(avg_payout, sum_insured, len(table)),
    }


class PriceEconomicsRequest(BaseModel):
    phases: list[dict]
    distribution: str = "gamma"
    loadings: list[dict] | None = None


@app.post("/products/drafts/{draft_id}/zones/{zone}/economics")
def zone_economics(draft_id: str, zone: int, req: PriceEconomicsRequest,
                   _: dict = Depends(ACTUARY)) -> dict:
    """Full pricing for one zone: expected loss + loadings -> commercial rate."""
    from app.db import connect
    from app.economics import compute_zone_economics

    with connect() as conn:
        row = conn.execute(
            "SELECT country, zone_map, years, definition FROM product_drafts WHERE id = %s",
            (draft_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, f"No product draft {draft_id}")
    country, zone_map, years, definition = row

    with connect() as conn:
        zrow = conn.execute(
            "SELECT geojson FROM zone_map_versions WHERE name = %s", (zone_map,)
        ).fetchone()
    zone_geojson = zrow[0]

    try:
        return compute_zone_economics(
            _store(), country, zone_geojson, years, definition["plant_start"],
            definition["sum_insured"], zone, req.phases,
            distribution=req.distribution, loadings=req.loadings, cache_key=draft_id,
        )
    except ValueError as e:
        # Invalid trigger terms (e.g. a strike edited past its exit) — send a
        # readable 400 the UI can show, not an opaque 500.
        raise HTTPException(400, str(e))


@app.get("/pricing/defaults")
def pricing_defaults(_: dict = Depends(ACTUARY)) -> dict:
    from app.pricing import DEFAULT_LOADINGS, DISTRIBUTIONS

    return {"loadings": DEFAULT_LOADINGS, "distributions": list(DISTRIBUTIONS)}


# ---------------------------------------------------------------------------
# Publish / registry (issue 011): freeze a draft into an immutable, versioned
# product with a per-zone rate table and an exportable assumption sheet.
# ---------------------------------------------------------------------------

class PublishRequest(BaseModel):
    distribution: str = "gamma"
    loadings: list[dict]
    zone_phases: dict | None = None  # {zone_id: phases} — the actuary's edits


@app.post("/products/drafts/{draft_id}/publish")
def publish_draft(draft_id: str, req: PublishRequest, user: dict = Depends(ACTUARY)) -> dict:
    from app.publish import PublishError, publish_product

    try:
        return publish_product(
            _store(), draft_id, req.distribution, req.loadings,
            req.zone_phases, user["username"],
        )
    except PublishError as e:
        raise HTTPException(400, str(e))


@app.get("/products/published")
def list_published_products(_: dict = Depends(AUTHED)) -> list[dict]:
    # Any signed-in user, incl. agents (the agent page lists what's quotable).
    from app.publish import list_published

    return list_published()


@app.get("/products/published/{product_id}")
def get_published_product(product_id: str, _: dict = Depends(INTERNAL)) -> dict:
    from app.publish import get_published

    product = get_published(product_id)
    if not product:
        raise HTTPException(404, f"No published product {product_id}")
    return product


@app.get("/products/published/{product_id}/assumption-sheet")
def published_assumption_sheet(product_id: str, _: dict = Depends(INTERNAL)):
    from fastapi.responses import HTMLResponse

    from app.publish import get_published, render_assumption_sheet_html

    product = get_published(product_id)
    if not product:
        raise HTTPException(404, f"No published product {product_id}")
    return HTMLResponse(render_assumption_sheet_html(product))


@app.get("/rates")
def query_rates(country: str, crop: str, season: str, zone: int | None = None,
                _: dict = Depends(INTERNAL)) -> list[dict]:
    """The quoting source: latest published rates by (country, crop, season[, zone])."""
    from app.publish import get_rates

    return get_rates(country, crop, season, zone)


# ---------------------------------------------------------------------------
# Quoting (issue 014): pin -> zone -> published rate -> premium, in under a
# second. Same service backs the partner API and the agent page. (Auth / agent
# scoping is issue 013.)
# ---------------------------------------------------------------------------

class QuoteRequest(BaseModel):
    country: str
    crop: str
    season: str
    sum_insured: float
    lat: float
    lon: float
    admin_area: str | None = None


@app.post("/quotes")
def create_quote_endpoint(req: QuoteRequest, user: dict = Depends(AGENT)) -> dict:
    from app.quotes import create_quote

    return create_quote(
        req.country, req.crop, req.season, req.sum_insured,
        req.lat, req.lon, req.admin_area, user["username"],
    )


@app.get("/quotes")
def list_quotes_endpoint(user: dict = Depends(AUTHED)) -> list[dict]:
    """An agent sees only their own quotes; operations/admin see all."""
    from app.quotes import list_quotes

    scope = None if user["role"] in ("operations", "admin") else user["username"]
    return list_quotes(scope)


@app.get("/quotes/{reference}")
def get_quote_endpoint(reference: str, user: dict = Depends(AUTHED)) -> dict:
    from app.quotes import get_quote

    q = get_quote(reference)
    if not q:
        raise HTTPException(404, f"No quote {reference}")
    # Agent scoping: you can read your own quote; operations/admin read any.
    if user["role"] not in ("operations", "admin") and q["created_by"] != user["username"]:
        raise HTTPException(403, "Not your quote")
    return q


@app.get("/quote-areas")
def quote_areas_endpoint(country: str, _: dict = Depends(AUTHED)) -> list[dict]:
    """Admin districts + centroids for the village-picker fallback."""
    from app.quotes import quote_areas

    return quote_areas(settings.weather_cache_dir, country)


@app.get("/demand-signals")
def demand_signals_endpoint(country: str | None = None,
                            _: dict = Depends(OPERATIONS)) -> list[dict]:
    from app.quotes import list_demand_signals

    return list_demand_signals(country)


@app.get("/agent")
def agent_page():
    """Lightweight, phone-friendly quoting page for field agents. The page shell
    is public; every action on it requires an agent login."""
    from fastapi.responses import HTMLResponse

    from app.quotes import AGENT_PAGE

    return HTMLResponse(AGENT_PAGE)


# ---------------------------------------------------------------------------
# Binding / policy register (issue 015): quotes -> master policy + schedule of
# farmers (PII encrypted at rest); premium receipt activates the policy.
# ---------------------------------------------------------------------------

class BindEntry(BaseModel):
    quote_reference: str | None = None
    zone: int | None = None
    sum_insured: float | None = None
    farmer: dict  # {name, phone, gender?, national_id?}


class BindRequest(BaseModel):
    sale_type: str = "individual"
    partner_name: str | None = None
    product_id: str | None = None
    entries: list[BindEntry]


@app.post("/policies")
def bind_policy_endpoint(req: BindRequest, user: dict = Depends(AGENT)) -> dict:
    from app.policies import BindError, bind_policy

    try:
        return bind_policy(
            req.sale_type, req.partner_name, req.product_id,
            [e.model_dump() for e in req.entries], user["username"],
        )
    except BindError as e:
        raise HTTPException(400, str(e))


@app.get("/policies")
def list_policies_endpoint(
    partner: str | None = None, product_id: str | None = None,
    status: str | None = None, agent: str | None = None, zone: int | None = None,
    user: dict = Depends(AUTHED),
) -> list[dict]:
    """Register: an agent sees only their own policies; operations/admin see all
    and can filter by partner, product, agent, status and zone."""
    from app.policies import list_policies

    scoped = None if user["role"] in ("operations", "admin") else user["username"]
    return list_policies(scoped, partner=partner, product_id=product_id,
                         status=status, agent=agent, zone=zone)


def _policy_or_403(policy_id: str, user: dict):
    from app.policies import get_master

    master = get_master(policy_id)
    if not master:
        raise HTTPException(404, f"No policy {policy_id}")
    if user["role"] not in ("operations", "admin") and master["created_by"] != user["username"]:
        raise HTTPException(403, "Not your policy")
    return master


@app.get("/policies/{policy_id}")
def get_policy_endpoint(policy_id: str, user: dict = Depends(AUTHED)) -> dict:
    from app.policies import get_policy

    _policy_or_403(policy_id, user)
    return get_policy(policy_id)


class ReceiptRequest(BaseModel):
    reference: str
    date: str | None = None


@app.post("/policies/{policy_id}/receipt")
def policy_receipt_endpoint(policy_id: str, req: ReceiptRequest,
                            user: dict = Depends(AUTHED)) -> dict:
    from app.policies import BindError, record_receipt

    _policy_or_403(policy_id, user)
    try:
        return record_receipt(policy_id, req.reference, req.date)
    except BindError as e:
        raise HTTPException(400, str(e))


class StatusRequest(BaseModel):
    status: str


@app.post("/policies/{policy_id}/status")
def policy_status_endpoint(policy_id: str, req: StatusRequest,
                           _: dict = Depends(OPERATIONS)) -> dict:
    from app.policies import BindError, set_status

    try:
        return set_status(policy_id, req.status)
    except BindError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# Settlement / season dashboard (issue 016): the season watched live through the
# same engine that priced the product. Provisional (from the in-season window)
# is clearly distinguished from settled (CHIRPS final, past the ~3-week lag);
# only settled values are ever persisted.
# ---------------------------------------------------------------------------

@app.get("/settlement/season")
def settlement_season(product_id: str, season_year: int | None = None,
                      _: dict = Depends(OPERATIONS)) -> dict:
    from app.settlement import season_view

    try:
        return season_view(_store(), product_id, season_year=season_year)
    except ValueError as e:
        raise HTTPException(404, str(e))


class SettlementRunRequest(BaseModel):
    product_id: str | None = None
    season_year: int | None = None


@app.post("/settlement/run")
def settlement_run(req: SettlementRunRequest, _: dict = Depends(OPERATIONS)) -> dict:
    """On-demand settlement sweep (the scheduled job runs the same code daily).
    Computes and persists any phase that has newly gone final."""
    from app.settlement import run_settlement_sweep

    return run_settlement_sweep(
        _store(), product_id=req.product_id, season_year=req.season_year
    )


# ---------------------------------------------------------------------------
# Payout run (issue 017): season close. Review the settled season (totals,
# farmer count, largest amounts, per-zone table, >3x-EL anomaly flags), then one
# human clicks Release — the run locks, policies flip to settled, and a payout
# file (phone, amount, policy, zone, index evidence) exports for the rails.
# ---------------------------------------------------------------------------

@app.get("/payouts/run")
def payout_run_review(product_id: str, season_year: int | None = None,
                      _: dict = Depends(OPERATIONS)) -> dict:
    from app.payout import PayoutError, build_payout_run

    try:
        return build_payout_run(product_id, season_year)
    except PayoutError as e:
        raise HTTPException(404, str(e))


class ReleaseRequest(BaseModel):
    product_id: str
    season_year: int | None = None
    confirm: bool = False


@app.post("/payouts/release")
def payout_release(req: ReleaseRequest, user: dict = Depends(OPERATIONS)) -> dict:
    """Release the season's payout file. Requires explicit confirmation; the run
    locks after release and cannot be released again."""
    from app.payout import PayoutError, release_payout_run

    if not req.confirm:
        raise HTTPException(400, "release must be explicitly confirmed")
    try:
        return release_payout_run(req.product_id, req.season_year, user["username"])
    except PayoutError as e:
        raise HTTPException(400, str(e))


@app.get("/payouts/runs")
def payout_runs_list(product_id: str | None = None,
                     _: dict = Depends(OPERATIONS)) -> list[dict]:
    from app.payout import list_runs

    return list_runs(product_id)


@app.get("/payouts/runs/{run_id}")
def payout_run_get(run_id: str, _: dict = Depends(OPERATIONS)) -> dict:
    from app.payout import get_run

    run = get_run(run_id)
    if not run:
        raise HTTPException(404, f"no payout run {run_id}")
    return run


@app.get("/payouts/runs/{run_id}/file")
def payout_run_file(run_id: str, _: dict = Depends(OPERATIONS)):
    """The disbursement CSV for the payment rails (documented columns)."""
    from fastapi.responses import PlainTextResponse

    from app.payout import get_run, render_payout_file

    if not get_run(run_id):
        raise HTTPException(404, f"no payout run {run_id}")
    csv_text = render_payout_file(run_id)
    return PlainTextResponse(
        csv_text, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{run_id}.csv"'},
    )


@app.post("/jobs/demo")
def start_demo_job(_: dict = Depends(AUTHED)) -> dict:
    task = demo_job.delay()
    return {"job_id": task.id}


@app.get("/jobs/{job_id}")
def job_status(job_id: str, _: dict = Depends(AUTHED)) -> dict:
    result = AsyncResult(job_id, app=celery_app)
    payload: dict = {"job_id": job_id, "state": result.state}
    if result.state == "PROGRESS":
        payload["progress"] = result.info
    elif result.successful():
        payload["result"] = result.result
    return payload
