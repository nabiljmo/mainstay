"""Quoting — a GPS pin (or picked village) to a premium.

The path is deliberately short so it answers in well under a second:

    point --(point-in-polygon over the product's frozen zone map)--> zone
    zone  --(published rate table)--> rate --> premium

A quote is persisted with a human reference, a link back to the exact product
version it came from, and a plain-language cover summary. A pin with no
published product (or one that falls outside the zone map) returns a friendly
"not yet available here" and logs a demand signal so operations can see where
cover is being asked for.

The same service backs both the partner REST API and the agent page; the
village-picker fallback simply resolves to a centroid and takes the identical
point path, so a picked village and a dropped pin at that spot quote the same.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app.db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
    reference       TEXT PRIMARY KEY,
    product_id      TEXT NOT NULL REFERENCES published_products(id),
    product_version INT NOT NULL,
    country         TEXT NOT NULL,
    crop            TEXT NOT NULL,
    season          TEXT NOT NULL,
    zone            INT NOT NULL,
    lat             DOUBLE PRECISION NOT NULL,
    lon             DOUBLE PRECISION NOT NULL,
    admin_area      TEXT,
    sum_insured     DOUBLE PRECISION NOT NULL,
    premium_rate    DOUBLE PRECISION NOT NULL,
    premium         DOUBLE PRECISION NOT NULL,
    cover_summary   TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS demand_signals (
    id          SERIAL PRIMARY KEY,
    country     TEXT NOT NULL,
    crop        TEXT NOT NULL,
    season      TEXT NOT NULL,
    lat         DOUBLE PRECISION,
    lon         DOUBLE PRECISION,
    admin_area  TEXT,
    reason      TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_COVER_WORDS = {
    "deficit": "too little rain",
    "excess": "too much rain",
    "dry_spell": "long dry spells",
}


def init_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA)


def zone_for_point(zone_geojson: dict, lat: float, lon: float) -> int | None:
    """Point-in-polygon: which zone covers (lat, lon)? None if outside them all."""
    from shapely.geometry import Point, shape

    pt = Point(lon, lat)
    for feat in zone_geojson["features"]:
        zone = feat["properties"].get("zone")
        if zone is None:
            continue
        if shape(feat["geometry"]).covers(pt):
            return int(zone)
    return None


def cover_summary(crop: str, season: str, phases: list[dict], sum_insured: float) -> str:
    """One plain-language sentence an agent can read to a farmer."""
    protections = []
    for p in phases:
        w = _COVER_WORDS.get(p.get("cover_type"), "poor rainfall")
        if w not in protections:
            protections.append(w)
    against = " and ".join(protections) if protections else "poor rainfall"
    season_words = season.replace("_", " ")
    return (
        f"{crop.title()} cover for the {season_words} season across "
        f"{len(phases)} growth stages. Pays out automatically when the season "
        f"brings {against}, up to {sum_insured:,.0f} of cover."
    )


def _reference(country: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%y%m%d")
    return f"Q-{country}-{stamp}-{uuid.uuid4().hex[:5].upper()}"


def _latest_product(country: str, crop: str, season: str):
    with connect() as conn:
        return conn.execute(
            """SELECT id, version, zone_map, definition
               FROM published_products
               WHERE country=%s AND crop=%s AND season=%s
               ORDER BY version DESC LIMIT 1""",
            (country, crop, season),
        ).fetchone()


def _log_demand(country, crop, season, lat, lon, admin_area, reason, created_by) -> int:
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO demand_signals
               (country, crop, season, lat, lon, admin_area, reason, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (country, crop, season, lat, lon, admin_area, reason, created_by),
        ).fetchone()
    return row[0]


def create_quote(country: str, crop: str, season: str, sum_insured: float,
                 lat: float, lon: float, admin_area: str | None,
                 created_by: str) -> dict:
    """Resolve a pin to a premium, or log demand when cover isn't available."""
    product = _latest_product(country, crop, season)
    if not product:
        sig = _log_demand(country, crop, season, lat, lon, admin_area, "no_product", created_by)
        return {
            "status": "no_product",
            "message": f"Weather cover for {crop} ({season.replace('_', ' ')}) "
                       f"is not yet available in {country}.",
            "demand_signal_id": sig,
        }
    product_id, version, zone_map, definition = product

    with connect() as conn:
        zrow = conn.execute(
            "SELECT geojson FROM zone_map_versions WHERE name = %s", (zone_map,)
        ).fetchone()
    zone = zone_for_point(zrow[0], lat, lon) if zrow else None

    if zone is None or str(zone) not in definition["zones"]:
        sig = _log_demand(country, crop, season, lat, lon, admin_area, "outside_coverage", created_by)
        return {
            "status": "outside_coverage",
            "message": "This location is outside the mapped area for this product. "
                       "We've noted the request.",
            "demand_signal_id": sig,
        }

    with connect() as conn:
        rrow = conn.execute(
            """SELECT premium_rate, quality_flag FROM published_rates
               WHERE product_id=%s AND zone=%s""",
            (product_id, zone),
        ).fetchone()
    premium_rate, quality_flag = rrow[0], rrow[1]
    premium = round(premium_rate / 100.0 * sum_insured, 2)
    summary = cover_summary(crop, season, definition["zones"][str(zone)]["phases"], sum_insured)

    reference = _reference(country)
    with connect() as conn:
        conn.execute(
            """INSERT INTO quotes
               (reference, product_id, product_version, country, crop, season, zone,
                lat, lon, admin_area, sum_insured, premium_rate, premium,
                cover_summary, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (reference, product_id, version, country, crop, season, zone,
             lat, lon, admin_area, sum_insured, premium_rate, premium,
             summary, created_by),
        )

    return {
        "status": "quoted",
        "reference": reference,
        "product_id": product_id,
        "product_version": version,
        "country": country,
        "crop": crop,
        "season": season,
        "zone": zone,
        "sum_insured": sum_insured,
        "premium_rate": premium_rate,
        "premium": premium,
        "quality_flag": quality_flag,
        "cover_summary": summary,
    }


def get_quote(reference: str) -> dict | None:
    with connect() as conn:
        r = conn.execute(
            """SELECT reference, product_id, product_version, country, crop, season,
                      zone, lat, lon, admin_area, sum_insured, premium_rate, premium,
                      cover_summary, created_by, created_at
               FROM quotes WHERE reference = %s""",
            (reference,),
        ).fetchone()
    if not r:
        return None
    return {
        "reference": r[0], "product_id": r[1], "product_version": r[2],
        "country": r[3], "crop": r[4], "season": r[5], "zone": r[6],
        "lat": r[7], "lon": r[8], "admin_area": r[9], "sum_insured": r[10],
        "premium_rate": r[11], "premium": r[12], "cover_summary": r[13],
        "created_by": r[14], "created_at": r[15].isoformat(),
    }


def list_quotes(created_by: str | None = None) -> list[dict]:
    """Quotes, optionally scoped to one creator (an agent sees only their own)."""
    sql = ("SELECT reference, product_id, product_version, country, crop, season, "
           "zone, admin_area, sum_insured, premium_rate, premium, created_by, created_at "
           "FROM quotes")
    params: list = []
    if created_by is not None:
        sql += " WHERE created_by = %s"
        params.append(created_by)
    sql += " ORDER BY created_at DESC"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {"reference": r[0], "product_id": r[1], "product_version": r[2], "country": r[3],
         "crop": r[4], "season": r[5], "zone": r[6], "admin_area": r[7], "sum_insured": r[8],
         "premium_rate": r[9], "premium": r[10], "created_by": r[11], "created_at": r[12].isoformat()}
        for r in rows
    ]


def list_demand_signals(country: str | None = None) -> list[dict]:
    sql = ("SELECT id, country, crop, season, lat, lon, admin_area, reason, "
           "created_by, created_at FROM demand_signals")
    params: list = []
    if country:
        sql += " WHERE country = %s"
        params.append(country)
    sql += " ORDER BY created_at DESC"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {"id": r[0], "country": r[1], "crop": r[2], "season": r[3], "lat": r[4],
         "lon": r[5], "admin_area": r[6], "reason": r[7], "created_by": r[8],
         "created_at": r[9].isoformat()}
        for r in rows
    ]


AGENT_PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Mainstay — get a quote</title>
<style>
  *{box-sizing:border-box;margin:0}
  body{font-family:system-ui,sans-serif;background:#eef3fa;color:#0f1f33;
       max-width:460px;margin:0 auto;padding:1rem;line-height:1.45}
  header{background:linear-gradient(118deg,#0b3a5b,#0f5686);color:#fff;
         margin:-1rem -1rem 1rem;padding:1.1rem 1.25rem;border-bottom:2px solid #1d9bf0}
  h1{font-size:1.15rem;font-weight:600}
  header p{font-size:.8rem;opacity:.85;margin-top:.15rem}
  label{display:block;font-size:.8rem;font-weight:600;color:#5f7089;margin:.9rem 0 .3rem}
  select,input{width:100%;padding:.7rem;font-size:1rem;border:1px solid #ccd7e6;
       border-radius:8px;background:#fff;font-family:inherit}
  select:focus,input:focus{outline:none;border-color:#1d9bf0;box-shadow:0 0 0 3px rgba(29,155,240,.25)}
  .loc-btn{background:#fff;color:#0a6cb0;border:1px solid #1d9bf0;font-weight:600;
       margin-top:.3rem;cursor:pointer}
  .loc-btn:active{background:#f1f9ff}
  .or{text-align:center;font-size:.75rem;color:#93a2b8;margin:.6rem 0;text-transform:uppercase;letter-spacing:.5px}
  .coords{font-size:.78rem;color:#15803d;margin-top:.4rem;min-height:1rem}
  #go{background:linear-gradient(180deg,#1d9bf0,#0d84d6);color:#fff;border:none;
       font-size:1.05rem;font-weight:700;padding:.85rem;border-radius:9px;
       margin-top:1.2rem;cursor:pointer;box-shadow:0 2px 8px rgba(29,155,240,.3)}
  #go:disabled{background:#c6cfdb;box-shadow:none}
  .buy{background:linear-gradient(180deg,#1d9bf0,#0d84d6);color:#fff;border:none;width:100%;
       font-size:1rem;font-weight:700;padding:.75rem;border-radius:9px;margin-top:1rem;cursor:pointer}
  .buy:disabled{background:#c6cfdb}
  .card{margin-top:1.2rem;background:#fff;border-radius:12px;padding:1.1rem;
       box-shadow:0 6px 18px rgba(16,31,51,.1)}
  .card.ok{border-top:4px solid #15a34a}
  .card.no{border-top:4px solid #e6a700}
  .premium{font-size:2rem;font-weight:800;color:#0b3a5b}
  .premium small{font-size:.9rem;font-weight:600;color:#5f7089}
  .ref{font-size:.75rem;color:#93a2b8;margin-top:.2rem;letter-spacing:.3px}
  .sum{font-size:.92rem;margin-top:.7rem}
  .qflag{display:inline-block;font-size:.68rem;font-weight:700;padding:.15rem .5rem;
       border-radius:99px;margin-top:.6rem}
  .qflag.red{background:#fee2e2;color:#991b1b}.qflag.amber{background:#fef3c7;color:#92400e}
  .qflag.green{background:#dcfce7;color:#14532d}
  .msg{font-size:.95rem;color:#92400e}
</style></head><body>
<header><h1>Mainstay</h1><p>Weather cover for your farm — pin it, pick a crop, see the premium.</p></header>

<div id="login">
  <label for="u">Username<input id="u" autocomplete="username"></label>
  <label for="p">Password<input id="p" type="password" autocomplete="current-password"></label>
  <button class="loc-btn" id="signin" type="button">Sign in</button>
  <div class="msg" id="lerr"></div>
</div>

<div id="app" style="display:none">
<label for="product">Product</label>
<select id="product"></select>

<label for="si">Sum insured</label>
<input id="si" type="number" inputmode="numeric" value="10000">

<label>Farm location</label>
<button class="loc-btn" id="gps" type="button">📍 Use my GPS location</button>
<div class="or">or pick a village</div>
<select id="village"><option value="">Loading villages…</option></select>
<div class="coords" id="coords"></div>

<button id="go" type="button" disabled>Get quote</button>
<div id="result"></div>
</div>

<script>
var products=[], sel={lat:null,lon:null,area:null};

// --- auth gate: the quote call needs an agent login ---
function showApp(){document.getElementById('login').style.display='none';
  document.getElementById('app').style.display='';init()}
fetch('/auth/me').then(function(r){if(r.ok){showApp()}else{document.getElementById('login').style.display=''}});
document.getElementById('signin').addEventListener('click',function(){
  fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:document.getElementById('u').value,password:document.getElementById('p').value})})
  .then(function(r){if(!r.ok)throw 0;return r.json()}).then(showApp)
  .catch(function(){document.getElementById('lerr').textContent='Login failed — check your details.'});
});

function init(){
function currentCountry(){var p=products[document.getElementById('product').value]; return p?p.country:null}
function refresh(){document.getElementById('go').disabled = !(sel.lat!=null && sel.lon!=null)}
function setCoords(lat,lon,area,label){sel={lat:lat,lon:lon,area:area||null};
  document.getElementById('coords').textContent=label+' ('+lat.toFixed(3)+', '+lon.toFixed(3)+')';refresh()}

fetch('/products/published').then(function(r){return r.json()}).then(function(list){
  // The list is newest-first; keep only the latest version per crop+season+country.
  var seen={}; products=list.filter(function(p){var k=p.country+'|'+p.crop+'|'+p.season;
    if(seen[k])return false; seen[k]=1; return true});
  var s=document.getElementById('product');
  if(!products.length){s.innerHTML='<option>No products published yet</option>';return}
  list=products;
  s.innerHTML=list.map(function(p,i){return '<option value="'+i+'">'+p.crop+' · '+
    p.season.replace('_',' ')+' ('+p.country+')</option>'}).join('');
  loadVillages();
});
document.getElementById('product').addEventListener('change',loadVillages);

function loadVillages(){var c=currentCountry(); var v=document.getElementById('village');
  v.innerHTML='<option value="">Loading villages…</option>';
  fetch('/quote-areas?country='+encodeURIComponent(c)).then(function(r){return r.json()}).then(function(a){
    if(!a.length){v.innerHTML='<option value="">(village list unavailable — use GPS)</option>';return}
    v.innerHTML='<option value="">— pick a village —</option>'+a.map(function(x){
      return '<option data-lat="'+x.lat+'" data-lon="'+x.lon+'" data-name="'+x.name+'">'+
        x.name+(x.region?' · '+x.region:'')+'</option>'}).join('');
  }).catch(function(){v.innerHTML='<option value="">(village list unavailable — use GPS)</option>'});
}
document.getElementById('village').addEventListener('change',function(e){
  var o=e.target.selectedOptions[0]; if(!o||!o.dataset.lat)return;
  setCoords(parseFloat(o.dataset.lat),parseFloat(o.dataset.lon),o.dataset.name,o.dataset.name);
});
document.getElementById('gps').addEventListener('click',function(){
  var c=document.getElementById('coords'); c.textContent='Locating…';
  if(!navigator.geolocation){c.textContent='GPS not available — pick a village';return}
  navigator.geolocation.getCurrentPosition(function(pos){
    setCoords(pos.coords.latitude,pos.coords.longitude,null,'GPS location');
  },function(){c.textContent='Could not get GPS — pick a village instead'});
});

document.getElementById('go').addEventListener('click',function(){
  var p=products[document.getElementById('product').value]; if(!p)return;
  var btn=this; btn.disabled=true; btn.textContent='Getting quote…';
  fetch('/quotes',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({country:p.country,crop:p.crop,season:p.season,
      sum_insured:parseFloat(document.getElementById('si').value)||0,
      lat:sel.lat,lon:sel.lon,admin_area:sel.area,created_by:'agent-page'})})
  .then(function(r){return r.json()}).then(render)
  .catch(function(){document.getElementById('result').innerHTML='<div class="card no"><div class="msg">Something went wrong. Try again.</div></div>'})
  .finally(function(){btn.disabled=false;btn.textContent='Get quote';refresh()});
});

function val(id){return document.getElementById(id).value.trim()}
function render(q){var el=document.getElementById('result');
  if(q.status!=='quoted'){el.innerHTML='<div class="card no"><div class="msg">'+q.message+'</div></div>';return}
  el.innerHTML='<div class="card ok"><div class="premium">'+q.premium.toLocaleString()+
    ' <small>premium</small></div><div class="ref">'+q.reference+' · zone '+q.zone+
    ' · rate '+q.premium_rate+'%</div><div class="sum">'+q.cover_summary+'</div>'+
    '<div class="qflag '+q.quality_flag+'">data quality: '+q.quality_flag+'</div>'+
    '<button class="buy" id="buy">Buy this cover</button></div><div id="bindbox"></div>';
  document.getElementById('buy').addEventListener('click',function(){startBind(q)});
}
function startBind(q){
  document.getElementById('bindbox').innerHTML='<div class="card">'+
    '<label>Farmer name<input id="fname" autocomplete="name"></label>'+
    '<label>Phone<input id="fphone" inputmode="tel"></label>'+
    '<label>Gender<select id="fgender"><option value="">—</option>'+
      '<option value="F">Female</option><option value="M">Male</option></select></label>'+
    '<label>National ID (optional)<input id="fid"></label>'+
    '<button class="buy" id="dobind">Bind policy</button>'+
    '<div class="msg" id="binderr"></div></div>';
  document.getElementById('dobind').addEventListener('click',function(){doBind(q)});
}
function doBind(q){
  var f={name:val('fname'),phone:val('fphone'),gender:val('fgender'),national_id:val('fid')};
  if(!f.name||!f.phone){document.getElementById('binderr').textContent='Name and phone are required.';return}
  var btn=document.getElementById('dobind');btn.disabled=true;btn.textContent='Binding…';
  fetch('/policies',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({sale_type:'individual',entries:[{quote_reference:q.reference,farmer:f}]})})
  .then(function(r){if(!r.ok)return r.json().then(function(b){throw new Error(b.detail||'Bind failed')});return r.json()})
  .then(showPolicy)
  .catch(function(e){document.getElementById('binderr').textContent=e.message})
  .finally(function(){btn.disabled=false;btn.textContent='Bind policy'});
}
function showPolicy(p){
  document.getElementById('bindbox').innerHTML='<div class="card ok">'+
    '<strong>Policy '+p.id+'</strong>'+
    '<div class="ref">status: '+p.status+' · premium '+p.total_premium.toLocaleString()+'</div>'+
    '<label>Payment reference (M-Pesa etc.)<input id="rcpt"></label>'+
    '<button class="buy" id="dorcpt">Record payment &amp; activate</button>'+
    '<div class="msg" id="rcpterr"></div></div>';
  document.getElementById('dorcpt').addEventListener('click',function(){doReceipt(p.id)});
}
function doReceipt(id){
  var ref=val('rcpt'); if(!ref){document.getElementById('rcpterr').textContent='Enter the payment reference.';return}
  var btn=document.getElementById('dorcpt');btn.disabled=true;btn.textContent='Recording…';
  fetch('/policies/'+id+'/receipt',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({reference:ref})})
  .then(function(r){if(!r.ok)return r.json().then(function(b){throw new Error(b.detail||'Failed')});return r.json()})
  .then(function(p){document.getElementById('bindbox').innerHTML='<div class="card ok">'+
    '<strong>✓ Policy '+p.id+' is active</strong>'+
    '<div class="sum">Cover is live. Payment reference '+p.receipt_ref+' recorded.</div></div>'})
  .catch(function(e){document.getElementById('rcpterr').textContent=e.message})
  .finally(function(){btn.disabled=false;btn.textContent='Record payment &amp; activate'});
}
}
</script>
</body></html>"""


def quote_areas(cache_dir, country: str) -> list[dict]:
    """Admin districts + centroids for the village-picker fallback. Each area
    resolves to its centroid, which then takes the same point path as a pin —
    so a picked village and a dropped pin quote identically. Reads the cached
    GADM boundaries; returns [] if they aren't cached yet."""
    from pathlib import Path

    from shapely.geometry import shape

    cache = Path(cache_dir) / "gadm" / f"{country}_2.json"
    if not cache.exists():
        return []
    gadm = json.loads(cache.read_text())
    areas = []
    for feat in gadm["features"]:
        name = feat["properties"].get("NAME_2") or feat["properties"].get("NAME_1", "?")
        region = feat["properties"].get("NAME_1")
        c = shape(feat["geometry"]).representative_point()
        areas.append({"name": name, "region": region,
                      "lat": round(c.y, 5), "lon": round(c.x, 5)})
    areas.sort(key=lambda a: (a["region"] or "", a["name"]))
    return areas
