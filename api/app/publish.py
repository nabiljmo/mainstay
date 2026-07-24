"""Publish — freeze a priced draft into an immutable, versioned product.

Publishing is the moment a draft stops being a live sandbox and becomes the
quoting source of truth. It:

  1. re-prices every zone from the exact terms the actuary settled on, so the
     stored rate is the number they saw;
  2. writes a read-only product version + a rate table keyed
     (country, crop, season, zone);
  3. records a full audit trail and an *assumption sheet* — every input that
     produced the rates (dataset, years, fetch dates, library versions,
     method, loadings) — which is what makes the blind validation defensible.

There is deliberately no update or delete path: a change means a new version.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.crops import get_version as get_crop_version
from app.db import connect
from app.economics import compute_zone_economics
from app.weather import BASE_URL, DATASET, PRODUCT

SCHEMA = """
CREATE TABLE IF NOT EXISTS published_products (
    id            TEXT PRIMARY KEY,
    draft_id      TEXT NOT NULL,
    country       TEXT NOT NULL,
    crop          TEXT NOT NULL,
    crop_version  INT NOT NULL,
    season        TEXT NOT NULL,
    zone_map      TEXT NOT NULL,
    version       INT NOT NULL,
    sum_insured   DOUBLE PRECISION NOT NULL,
    years         JSONB NOT NULL,
    distribution  TEXT NOT NULL,
    loadings      JSONB NOT NULL,
    definition    JSONB NOT NULL,
    assumptions   JSONB NOT NULL,
    audit         JSONB NOT NULL,
    published_by  TEXT NOT NULL,
    published_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (country, crop, season, version)
);

CREATE TABLE IF NOT EXISTS published_rates (
    product_id    TEXT NOT NULL REFERENCES published_products(id),
    country       TEXT NOT NULL,
    crop          TEXT NOT NULL,
    season        TEXT NOT NULL,
    zone          INT NOT NULL,
    sum_insured   DOUBLE PRECISION NOT NULL,
    premium_rate  DOUBLE PRECISION NOT NULL,
    gross_premium DOUBLE PRECISION NOT NULL,
    expected_loss DOUBLE PRECISION NOT NULL,
    burning_cost  DOUBLE PRECISION NOT NULL,
    technical_el  DOUBLE PRECISION NOT NULL,
    quality_flag  TEXT NOT NULL,
    PRIMARY KEY (product_id, zone)
);
CREATE INDEX IF NOT EXISTS idx_published_rates_key
    ON published_rates (country, crop, season, zone);
"""


def init_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA)


class PublishError(Exception):
    """A zone failed to price (e.g. invalid trigger terms). Message is UI-safe."""


def _assumption_sheet(store, draft, zone_map_row, distribution, loadings, published_by):
    """Every input that produced the rates — the blind-validation artefact."""
    country, zone_map, crop, crop_version, season, years, sum_insured, definition = draft
    crop_rec = get_crop_version(crop, crop_version) or {}

    # Per-year CHIRPS provenance (fetch date etc.) from the weather cache meta.
    dataset_years = []
    for y in years:
        entry = {"year": y, "fetched_at": None, "missing_days": None}
        try:
            meta = store.meta(country, y)
            entry["fetched_at"] = meta.get("fetched_at")
            entry["missing_days"] = len(meta.get("missing_days", []))
        except Exception:
            pass
        dataset_years.append(entry)

    zm_name, zm_params, zm_by, zm_at = zone_map_row

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "published_by": published_by,
        "product": {
            "country": country,
            "crop": crop,
            "crop_version": crop_version,
            "crop_source": crop_rec.get("source"),
            "crop_reviewed": crop_rec.get("reviewed"),
            "season": season,
            "plant_start": definition.get("plant_start"),
            "sum_insured": sum_insured,
            "pricing_years": years,
        },
        "dataset": {
            "name": DATASET,
            "product": PRODUCT,
            "source": BASE_URL,
            "years": dataset_years,
        },
        "zone_map": {
            "name": zm_name,
            "n_clusters": zm_params.get("n_clusters"),
            "years": zm_params.get("years"),
            "sensitivity": zm_params.get("sensitivity"),
            "admin_snap": zm_params.get("admin_snap"),
            "approved_by": zm_by,
            "approved_at": zm_at.isoformat() if hasattr(zm_at, "isoformat") else zm_at,
        },
        "method": {
            "index": "area-average daily CHIRPS rainfall per zone",
            "expected_loss": "technical EL = max(burning cost, modelled EL)",
            "distribution": distribution,
            "gross_up": "gross = base / (1 - sum of %-of-gross loadings)",
            "loadings": loadings,
        },
    }


def publish_product(store, draft_id: str, distribution: str, loadings: list[dict],
                    zone_phases: dict | None, published_by: str) -> dict:
    """Freeze a draft into the next version. `zone_phases` optionally overrides
    the stored phases for specific zones (the terms the actuary edited this
    session, keyed by zone id as a string)."""
    zone_phases = zone_phases or {}

    with connect() as conn:
        row = conn.execute(
            """SELECT country, zone_map, crop, crop_version, season, years,
                      sum_insured, definition FROM product_drafts WHERE id = %s""",
            (draft_id,),
        ).fetchone()
    if not row:
        raise PublishError(f"No product draft {draft_id}")
    country, zone_map, crop, crop_version, season, years, sum_insured, definition = row

    with connect() as conn:
        zrow = conn.execute(
            "SELECT name, params, approved_by, approved_at, geojson "
            "FROM zone_map_versions WHERE name = %s", (zone_map,)
        ).fetchone()
    if not zrow:
        raise PublishError(f"Zone map {zone_map} not found")
    zone_geojson = zrow[4]

    # Price every zone from the settled terms (edits override the proposal).
    # Collect *all* invalid zones so the actuary sees the whole fix-list at once,
    # rather than discovering them one publish attempt at a time.
    frozen_zones = {}
    rate_rows = []
    failures = []
    for zid_str, zdef in definition["zones"].items():
        zone = int(zid_str)
        phases = zone_phases.get(zid_str) or zone_phases.get(zone) or zdef["phases"]
        try:
            econ = compute_zone_economics(
                store, country, zone_geojson, years, definition["plant_start"],
                sum_insured, zone, phases, distribution=distribution,
                loadings=loadings, cache_key=draft_id, explanations=False,
            )
        except ValueError as e:
            failures.append(f"Zone {zone}: {e}")
            continue
        frozen_zones[zid_str] = {"phases": phases}
        price, e = econ["price"], econ["economics"]
        rate_rows.append({
            "zone": zone,
            "premium_rate": price["premium_rate"],
            "gross_premium": price["gross_premium"],
            "expected_loss": e["technical_el"],
            "burning_cost": e["burning_cost"],
            "technical_el": e["technical_el"],
            "quality_flag": econ["quality_flag"],
        })

    if failures:
        raise PublishError(
            f"{len(failures)} zone(s) have invalid terms and must be fixed before "
            f"publishing: " + "; ".join(failures)
        )

    frozen_definition = {
        "country": country,
        "years": years,
        "sum_insured": sum_insured,
        "plant_start": definition["plant_start"],
        "phase_layout": definition.get("phase_layout"),
        "zones": frozen_zones,
    }
    assumptions = _assumption_sheet(
        store, row, (zrow[0], zrow[1], zrow[2], zrow[3]), distribution, loadings, published_by)
    audit = {
        "action": "publish",
        "by": published_by,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "draft_id": draft_id,
        "distribution": distribution,
        "loadings": loadings,
        "edited_zones": sorted(str(k) for k in zone_phases.keys()),
        "n_zones": len(rate_rows),
    }

    # Next version for this (country, crop, season) and immutable insert.
    with connect() as conn:
        with conn.transaction():
            ver_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM published_products "
                "WHERE country=%s AND crop=%s AND season=%s",
                (country, crop, season),
            ).fetchone()
            version = ver_row[0] + 1
            product_id = f"{country}-{crop}-{season}-v{version}"
            conn.execute(
                """INSERT INTO published_products
                   (id, draft_id, country, crop, crop_version, season, zone_map,
                    version, sum_insured, years, distribution, loadings,
                    definition, assumptions, audit, published_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (product_id, draft_id, country, crop, crop_version, season, zone_map,
                 version, sum_insured, json.dumps(years), distribution,
                 json.dumps(loadings), json.dumps(frozen_definition),
                 json.dumps(assumptions), json.dumps(audit), published_by),
            )
            for r in rate_rows:
                conn.execute(
                    """INSERT INTO published_rates
                       (product_id, country, crop, season, zone, sum_insured,
                        premium_rate, gross_premium, expected_loss, burning_cost,
                        technical_el, quality_flag)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (product_id, country, crop, season, r["zone"], sum_insured,
                     r["premium_rate"], r["gross_premium"], r["expected_loss"],
                     r["burning_cost"], r["technical_el"], r["quality_flag"]),
                )

    return {
        "id": product_id,
        "version": version,
        "country": country,
        "crop": crop,
        "season": season,
        "n_zones": len(rate_rows),
        "rates": sorted(rate_rows, key=lambda r: r["zone"]),
        "published_by": published_by,
    }


def list_published() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, country, crop, crop_version, season, zone_map, version,
                      sum_insured, published_by, published_at
               FROM published_products ORDER BY published_at DESC"""
        ).fetchall()
    return [
        {
            "id": r[0], "country": r[1], "crop": r[2], "crop_version": r[3],
            "season": r[4], "zone_map": r[5], "version": r[6], "sum_insured": r[7],
            "published_by": r[8], "published_at": r[9].isoformat(),
        }
        for r in rows
    ]


def get_published(product_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """SELECT id, draft_id, country, crop, crop_version, season, zone_map,
                      version, sum_insured, years, distribution, loadings,
                      definition, assumptions, audit, published_by, published_at
               FROM published_products WHERE id = %s""",
            (product_id,),
        ).fetchone()
        if not row:
            return None
        rates = conn.execute(
            """SELECT zone, premium_rate, gross_premium, expected_loss,
                      burning_cost, technical_el, quality_flag
               FROM published_rates WHERE product_id = %s ORDER BY zone""",
            (product_id,),
        ).fetchall()
    return {
        "id": row[0], "draft_id": row[1], "country": row[2], "crop": row[3],
        "crop_version": row[4], "season": row[5], "zone_map": row[6], "version": row[7],
        "sum_insured": row[8], "years": row[9], "distribution": row[10],
        "loadings": row[11], "definition": row[12], "assumptions": row[13],
        "audit": row[14], "published_by": row[15], "published_at": row[16].isoformat(),
        "rates": [
            {
                "zone": t[0], "premium_rate": t[1], "gross_premium": t[2],
                "expected_loss": t[3], "burning_cost": t[4], "technical_el": t[5],
                "quality_flag": t[6],
            }
            for t in rates
        ],
    }


def _esc(v) -> str:
    return "" if v is None else str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_assumption_sheet_html(product: dict) -> str:
    """A self-contained, printable assumption sheet. Open in a browser and
    'Print → Save as PDF' to export — the blind-validation record."""
    a = product["assumptions"]
    p, ds, zm, m = a["product"], a["dataset"], a["zone_map"], a["method"]

    ds_rows = "".join(
        f"<tr><td>{_esc(y['year'])}</td><td>{_esc(y['fetched_at']) or '—'}</td>"
        f"<td>{_esc(y['missing_days']) if y['missing_days'] is not None else '—'}</td></tr>"
        for y in ds["years"]
    )
    load_rows = "".join(
        f"<tr><td>{_esc(l['name'])}</td><td>{_esc(l['basis'])}</td><td>{_esc(l['value'])}</td></tr>"
        for l in m["loadings"]
    )
    rate_rows = "".join(
        f"<tr><td>Zone {_esc(r['zone'])}</td><td>{_esc(r['premium_rate'])}%</td>"
        f"<td>{_esc(r['gross_premium'])}</td><td>{_esc(r['expected_loss'])}</td>"
        f"<td>{_esc(r['quality_flag'])}</td></tr>"
        for r in product["rates"]
    )
    reviewed = "reviewed ✓" if p.get("crop_reviewed") else "UNREVIEWED"

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Assumption sheet — {_esc(product['id'])}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui, sans-serif; color: #0f1f33; max-width: 860px;
         margin: 2rem auto; padding: 0 1.5rem; line-height: 1.5; }}
  header {{ border-bottom: 3px solid #1d9bf0; padding-bottom: 1rem; margin-bottom: 1.5rem; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 0.25rem; }}
  h2 {{ font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.6px;
        color: #5f7089; margin: 1.75rem 0 0.6rem; }}
  .sub {{ color: #5f7089; font-size: 0.9rem; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.88rem; margin-top: 0.4rem; }}
  th, td {{ border: 1px solid #e3eaf3; padding: 0.4rem 0.6rem; text-align: left; }}
  th {{ background: #f6f9fd; font-weight: 600; }}
  .kv {{ display: grid; grid-template-columns: 200px 1fr; gap: 0.35rem 1rem; font-size: 0.9rem; }}
  .kv dt {{ color: #5f7089; }}
  .kv dd {{ margin: 0; font-weight: 500; }}
  .flag-red {{ color: #dc2626; font-weight: 600; }}
  footer {{ margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #e3eaf3;
           color: #93a2b8; font-size: 0.8rem; }}
  @media print {{ body {{ margin: 0; }} h2 {{ break-after: avoid; }} }}
</style></head><body>
<header>
  <h1>Assumption Sheet</h1>
  <div class="sub">{_esc(product['id'])} · version {_esc(product['version'])}
    · published by {_esc(product['published_by'])} · {_esc(product['published_at'])}</div>
</header>

<h2>Product</h2>
<dl class="kv">
  <dt>Country</dt><dd>{_esc(p['country'])}</dd>
  <dt>Crop</dt><dd>{_esc(p['crop'])} v{_esc(p['crop_version'])} ({reviewed})</dd>
  <dt>Crop source</dt><dd>{_esc(p['crop_source'])}</dd>
  <dt>Season</dt><dd>{_esc(p['season'])} · plant {_esc(p['plant_start'])}</dd>
  <dt>Sum insured</dt><dd>{_esc(p['sum_insured'])}</dd>
  <dt>Pricing years</dt><dd>{_esc(', '.join(str(y) for y in p['pricing_years']))}</dd>
</dl>

<h2>Rainfall dataset</h2>
<dl class="kv">
  <dt>Dataset</dt><dd>{_esc(ds['name'])} — {_esc(ds['product'])}</dd>
  <dt>Source</dt><dd>{_esc(ds['source'])}</dd>
</dl>
<table><thead><tr><th>Year</th><th>Fetched at</th><th>Missing days</th></tr></thead>
<tbody>{ds_rows}</tbody></table>

<h2>Zone map</h2>
<dl class="kv">
  <dt>Version</dt><dd>{_esc(zm['name'])}</dd>
  <dt>Zones</dt><dd>{_esc(zm['n_clusters'])}</dd>
  <dt>Built from years</dt><dd>{_esc(', '.join(str(y) for y in (zm['years'] or [])))}</dd>
  <dt>Sensitivity</dt><dd>{_esc(zm['sensitivity'])}</dd>
  <dt>District-aligned</dt><dd>{_esc(zm['admin_snap'])}</dd>
  <dt>Approved by</dt><dd>{_esc(zm['approved_by'])} · {_esc(zm['approved_at'])}</dd>
</dl>

<h2>Method</h2>
<dl class="kv">
  <dt>Index</dt><dd>{_esc(m['index'])}</dd>
  <dt>Expected loss</dt><dd>{_esc(m['expected_loss'])}</dd>
  <dt>Severity fit</dt><dd>{_esc(m['distribution'])}</dd>
  <dt>Gross-up</dt><dd>{_esc(m['gross_up'])}</dd>
</dl>
<table><thead><tr><th>Loading</th><th>Basis</th><th>Value</th></tr></thead>
<tbody>{load_rows}</tbody></table>

<h2>Published rate table</h2>
<table><thead><tr><th>Zone</th><th>Rate</th><th>Gross premium</th>
<th>Expected loss</th><th>Data quality</th></tr></thead>
<tbody>{rate_rows}</tbody></table>

<footer>Generated {_esc(a['generated_at'])}. This sheet lists every input behind
the rates above. Rates were produced by the platform before any external
benchmark was consulted.</footer>
</body></html>"""


def get_rates(country: str, crop: str, season: str, zone: int | None = None) -> list[dict]:
    """The quoting source: published rates keyed (country, crop, season[, zone]).
    Returns the newest version's rates when several exist."""
    with connect() as conn:
        latest = conn.execute(
            """SELECT id FROM published_products
               WHERE country=%s AND crop=%s AND season=%s
               ORDER BY version DESC LIMIT 1""",
            (country, crop, season),
        ).fetchone()
        if not latest:
            return []
        product_id = latest[0]
        params = [product_id]
        sql = """SELECT zone, sum_insured, premium_rate, gross_premium,
                        expected_loss, quality_flag
                 FROM published_rates WHERE product_id = %s"""
        if zone is not None:
            sql += " AND zone = %s"
            params.append(zone)
        sql += " ORDER BY zone"
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "product_id": product_id, "zone": r[0], "sum_insured": r[1],
            "premium_rate": r[2], "gross_premium": r[3], "expected_loss": r[4],
            "quality_flag": r[5],
        }
        for r in rows
    ]
