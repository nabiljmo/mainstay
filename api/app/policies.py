"""Binding — the two-level policy register.

A **master policy** (a partner deal or an individual sale) sits above a
**schedule** of farmers. Each schedule row carries the cover terms and minimal
PII (name, phone, optional national ID) stored encrypted at rest. Binding turns
quotes into schedule rows; recording a premium receipt flips the master from
draft to active. No money is ever moved here — a receipt is just a reference we
record.

Two-level integrity is enforced by the schedule's foreign key (a schedule row
cannot exist without its master) and by writing master + schedule in one
transaction.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from app.crypto import decrypt, encrypt
from app.db import connect

STATUSES = ("draft", "active", "expired", "settled")

# Cover must be bound at least this long before planting. Sales for a season
# close on (planting date - buffer), so a policy can never attach to a season
# already under way — the guard against buying after the loss has occurred.
DEFAULT_SALES_CUTOFF_DAYS = 14


def covered_season(plant_start: str, today: date,
                   cutoff_days: int = DEFAULT_SALES_CUTOFF_DAYS) -> tuple[int, date]:
    """The season a policy bound `today` covers: the next planting whose sales
    window is still open. Once this year's window has closed, the sale rolls to
    next year's season — so a mid-season buyer is covered for the season *ahead*,
    never the one already running (and already in loss)."""
    month, day = (int(x) for x in plant_start.split("-"))
    year = today.year
    cutoff = date(year, month, day) - timedelta(days=cutoff_days)
    if today > cutoff:
        year += 1
        cutoff = date(year, month, day) - timedelta(days=cutoff_days)
    return year, cutoff
# Allowed manual transitions (draft->active happens via receipt, not here).
_TRANSITIONS = {
    ("active", "expired"),
    ("active", "settled"),
    ("draft", "expired"),   # lapsed unpaid
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS master_policies (
    id                TEXT PRIMARY KEY,
    sale_type         TEXT NOT NULL,          -- 'individual' | 'partner'
    partner_name      TEXT,
    product_id        TEXT NOT NULL REFERENCES published_products(id),
    country           TEXT NOT NULL,
    crop              TEXT NOT NULL,
    season            TEXT NOT NULL,
    season_year       INT,                    -- the season instance this covers
    status            TEXT NOT NULL DEFAULT 'draft',
    receipt_ref       TEXT,
    receipt_date      DATE,
    total_sum_insured DOUBLE PRECISION NOT NULL,
    total_premium     DOUBLE PRECISION NOT NULL,
    created_by        TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS policy_schedule (
    id               SERIAL PRIMARY KEY,
    master_policy_id TEXT NOT NULL REFERENCES master_policies(id) ON DELETE CASCADE,
    quote_reference  TEXT,
    zone             INT NOT NULL,
    sum_insured      DOUBLE PRECISION NOT NULL,
    premium_rate     DOUBLE PRECISION NOT NULL,
    premium          DOUBLE PRECISION NOT NULL,
    name_enc         TEXT NOT NULL,
    phone_enc        TEXT NOT NULL,
    email_enc        TEXT,
    national_id_enc  TEXT,
    gender           TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_schedule_master ON policy_schedule (master_policy_id);
CREATE INDEX IF NOT EXISTS idx_master_nav ON master_policies (country, product_id, status, created_by);
"""


def init_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA)
        # Backfill the column onto pre-existing tables (CREATE IF NOT EXISTS
        # never alters an existing table).
        conn.execute("ALTER TABLE master_policies ADD COLUMN IF NOT EXISTS season_year INT")
        conn.execute("ALTER TABLE policy_schedule ADD COLUMN IF NOT EXISTS email_enc TEXT")


class BindError(Exception):
    """A bind was rejected for a reason the UI can show."""


def _reference(country: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%y%m%d")
    return f"MP-{country}-{stamp}-{uuid.uuid4().hex[:5].upper()}"


def _resolve_entry(entry: dict, product_id: str, created_by: str) -> dict:
    """Turn a bind entry into priced schedule fields. Prefers the originating
    quote; otherwise prices from the published rate for the given zone."""
    farmer = entry.get("farmer") or {}
    name, phone = (farmer.get("name") or "").strip(), (farmer.get("phone") or "").strip()
    if not name or not phone:
        raise BindError("each farmer needs a name and phone number")

    ref = entry.get("quote_reference")
    if ref:
        with connect() as conn:
            q = conn.execute(
                """SELECT product_id, zone, sum_insured, premium_rate, premium, created_by
                   FROM quotes WHERE reference = %s""", (ref,)
            ).fetchone()
        if not q:
            raise BindError(f"quote {ref} not found")
        if q[0] != product_id:
            raise BindError(f"quote {ref} is for a different product")
        if q[5] != created_by:
            raise BindError(f"quote {ref} was not created by you")
        zone, sum_insured, premium_rate, premium = q[1], q[2], q[3], q[4]
    else:
        zone = entry.get("zone")
        sum_insured = entry.get("sum_insured")
        if zone is None or sum_insured is None:
            raise BindError("entry needs a quote_reference, or a zone and sum_insured")
        with connect() as conn:
            r = conn.execute(
                "SELECT premium_rate FROM published_rates WHERE product_id=%s AND zone=%s",
                (product_id, zone)).fetchone()
        if not r:
            raise BindError(f"no published rate for zone {zone}")
        premium_rate = r[0]
        premium = round(premium_rate / 100.0 * float(sum_insured), 2)

    return {
        "quote_reference": ref,
        "zone": int(zone),
        "sum_insured": float(sum_insured),
        "premium_rate": premium_rate,
        "premium": premium,
        "name_enc": encrypt(name),
        "phone_enc": encrypt(phone),
        "email_enc": encrypt((farmer.get("email") or "").strip() or None),
        "national_id_enc": encrypt((farmer.get("national_id") or "").strip() or None),
        "gender": (farmer.get("gender") or None),
    }


def bind_policy(sale_type: str, partner_name: str | None, product_id: str | None,
                entries: list[dict], created_by: str) -> dict:
    if sale_type not in ("individual", "partner"):
        raise BindError("sale_type must be 'individual' or 'partner'")
    if not entries:
        raise BindError("a policy needs at least one farmer")
    if sale_type == "individual" and len(entries) != 1:
        raise BindError("an individual sale is exactly one farmer")
    if sale_type == "partner" and not (partner_name or "").strip():
        raise BindError("a partner sale needs a partner name")

    # Derive the product from the quotes when not given explicitly.
    if not product_id:
        refs = [e.get("quote_reference") for e in entries if e.get("quote_reference")]
        if not refs:
            raise BindError("product_id is required when entries have no quotes")
        with connect() as conn:
            row = conn.execute("SELECT product_id FROM quotes WHERE reference = %s",
                               (refs[0],)).fetchone()
        if not row:
            raise BindError(f"quote {refs[0]} not found")
        product_id = row[0]

    with connect() as conn:
        prod = conn.execute(
            "SELECT country, crop, season, definition FROM published_products WHERE id = %s",
            (product_id,)).fetchone()
    if not prod:
        raise BindError(f"no published product {product_id}")
    country, crop, season, definition = prod

    # Stamp the season this cover applies to: the next one whose sales window is
    # still open. This is what stops a policy attaching to a season already in
    # progress (and already in loss).
    plant_start = (definition or {}).get("plant_start")
    season_year, sales_cutoff = (None, None)
    if plant_start:
        cutoff_days = int((definition or {}).get("sales_cutoff_days", DEFAULT_SALES_CUTOFF_DAYS))
        season_year, sales_cutoff = covered_season(plant_start, date.today(), cutoff_days)

    resolved = [_resolve_entry(e, product_id, created_by) for e in entries]
    total_si = round(sum(r["sum_insured"] for r in resolved), 2)
    total_prem = round(sum(r["premium"] for r in resolved), 2)
    policy_id = _reference(country)

    with connect() as conn:
        with conn.transaction():
            conn.execute(
                """INSERT INTO master_policies
                   (id, sale_type, partner_name, product_id, country, crop, season,
                    season_year, status, total_sum_insured, total_premium, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'draft',%s,%s,%s)""",
                (policy_id, sale_type, partner_name, product_id, country, crop, season,
                 season_year, total_si, total_prem, created_by))
            for r in resolved:
                conn.execute(
                    """INSERT INTO policy_schedule
                       (master_policy_id, quote_reference, zone, sum_insured,
                        premium_rate, premium, name_enc, phone_enc, email_enc,
                        national_id_enc, gender)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (policy_id, r["quote_reference"], r["zone"], r["sum_insured"],
                     r["premium_rate"], r["premium"], r["name_enc"], r["phone_enc"],
                     r["email_enc"], r["national_id_enc"], r["gender"]))

    return {
        "id": policy_id, "sale_type": sale_type, "partner_name": partner_name,
        "product_id": product_id, "country": country, "crop": crop, "season": season,
        "season_year": season_year,
        "sales_cutoff": sales_cutoff.isoformat() if sales_cutoff else None,
        "status": "draft", "farmers": len(resolved),
        "total_sum_insured": total_si, "total_premium": total_prem,
    }


def record_receipt(policy_id: str, reference: str, receipt_date: str | None,
                   ) -> dict:
    if not (reference or "").strip():
        raise BindError("a receipt needs a reference")
    with connect() as conn:
        row = conn.execute("SELECT status FROM master_policies WHERE id = %s",
                           (policy_id,)).fetchone()
        if not row:
            raise BindError(f"no policy {policy_id}")
        if row[0] != "draft":
            raise BindError(f"policy is already {row[0]} — only a draft can be activated")
        conn.execute(
            "UPDATE master_policies SET receipt_ref=%s, receipt_date=%s, status='active' "
            "WHERE id=%s",
            (reference, receipt_date or date.today().isoformat(), policy_id))
    return get_master(policy_id)


def set_status(policy_id: str, new_status: str) -> dict:
    if new_status not in STATUSES:
        raise BindError(f"unknown status {new_status!r}")
    with connect() as conn:
        row = conn.execute("SELECT status FROM master_policies WHERE id = %s",
                           (policy_id,)).fetchone()
        if not row:
            raise BindError(f"no policy {policy_id}")
        current = row[0]
        if (current, new_status) not in _TRANSITIONS:
            raise BindError(f"cannot move a policy from {current} to {new_status}")
        conn.execute("UPDATE master_policies SET status=%s WHERE id=%s",
                     (new_status, policy_id))
    return get_master(policy_id)


def _master_row(r) -> dict:
    return {
        "id": r[0], "sale_type": r[1], "partner_name": r[2], "product_id": r[3],
        "country": r[4], "crop": r[5], "season": r[6], "status": r[7],
        "receipt_ref": r[8], "receipt_date": r[9].isoformat() if r[9] else None,
        "total_sum_insured": r[10], "total_premium": r[11], "created_by": r[12],
        "created_at": r[13].isoformat(), "season_year": r[14],
    }


_MASTER_COLS = ("id, sale_type, partner_name, product_id, country, crop, season, status, "
                "receipt_ref, receipt_date, total_sum_insured, total_premium, created_by, "
                "created_at, season_year")


def get_master(policy_id: str) -> dict | None:
    with connect() as conn:
        r = conn.execute(f"SELECT {_MASTER_COLS} FROM master_policies WHERE id=%s",
                         (policy_id,)).fetchone()
    return _master_row(r) if r else None


def get_policy(policy_id: str) -> dict | None:
    """Master + schedule with PII decrypted. Callers must authorise first."""
    master = get_master(policy_id)
    if not master:
        return None
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, quote_reference, zone, sum_insured, premium_rate, premium,
                      name_enc, phone_enc, national_id_enc, gender, email_enc
               FROM policy_schedule WHERE master_policy_id=%s ORDER BY id""",
            (policy_id,)).fetchall()
    master["schedule"] = [
        {
            "id": s[0], "quote_reference": s[1], "zone": s[2], "sum_insured": s[3],
            "premium_rate": s[4], "premium": s[5],
            "farmer": {"name": decrypt(s[6]), "phone": decrypt(s[7]),
                       "national_id": decrypt(s[8]), "gender": s[9],
                       "email": decrypt(s[10])},
        }
        for s in rows
    ]
    return master


def _esc(v) -> str:
    return "" if v is None else str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_policy_document(policy_id: str) -> str | None:
    """A printable policy schedule for a bound policy — the farmer's proof of
    cover (open in a browser, Print → Save as PDF). Every factual term comes
    from the system; the legal wording is left as clearly-marked placeholders
    for the insurer's own counsel to complete — this is not legal advice."""
    from datetime import date as _date

    from app.explain import explain_phase
    from app.index_engine import COVER_DESCRIPTIONS, phase_from_dict
    from app.publish import get_published

    master = get_policy(policy_id)  # decrypted schedule; caller must authorise first
    if not master:
        return None
    product = get_published(master["product_id"]) or {}
    definition = product.get("definition", {})
    plant_start = definition.get("plant_start")
    zones_def = definition.get("zones", {})
    season_year = master.get("season_year")

    def _window(start_offset: int, end_offset: int) -> str:
        if plant_start and season_year:
            m, d = (int(x) for x in plant_start.split("-"))
            plant = _date(season_year, m, d)
            a = plant + timedelta(days=start_offset)
            b = plant + timedelta(days=end_offset - 1)
            return f"{a.isoformat()} → {b.isoformat()}"
        return f"day {start_offset}–{end_offset} after planting"

    # Insured schedule (the farmers under this master policy).
    sched_rows = "".join(
        f"<tr><td>{_esc(s['farmer']['name'])}</td><td>{_esc(s['farmer']['phone'])}</td>"
        f"<td>Zone {_esc(s['zone'])}</td><td>{_esc(f'{s['sum_insured']:,.0f}')}</td>"
        f"<td>{_esc(f'{s['premium']:,.0f}')}</td></tr>"
        for s in master["schedule"]
    )

    # Cover terms for each zone present on the policy.
    zones_present = sorted({s["zone"] for s in master["schedule"]})
    terms_blocks = []
    for zone in zones_present:
        zdef = zones_def.get(str(zone)) or zones_def.get(zone)
        if not zdef:
            continue
        rows = []
        for p in zdef["phases"]:
            rp = phase_from_dict({**p, "trigger_mode": p.get("trigger_mode", "absolute")})
            covered = float(p.get("limit", 0) or 0) > 0
            meaning = explain_phase(rp.name, rp.cover_type, p.get("reference"),
                                    rp.strike, rp.exit_, rp.limit)
            rows.append(
                f"<tr class='{'off' if not covered else ''}'>"
                f"<td>{_esc(rp.name.replace('_', ' '))}"
                f"{'' if covered else ' <em>(not covered)</em>'}</td>"
                f"<td>{_esc(rp.cover_type.replace('_', ' '))}</td>"
                f"<td>{_esc(_window(rp.start_offset, rp.end_offset))}</td>"
                f"<td>{_esc(f'{rp.limit:,.0f}')}</td>"
                f"<td class='mean'>{_esc(meaning)}</td></tr>")
        terms_blocks.append(
            f"<h3>Zone {_esc(zone)} — cover terms</h3>"
            f"<table><thead><tr><th>Stage</th><th>Cover</th><th>Window</th>"
            f"<th>Max payout</th><th>What it means</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")
    terms_html = "".join(terms_blocks) or "<p>Cover terms unavailable.</p>"

    glossary = "".join(f"<li><strong>{_esc(k)}:</strong> {_esc(v)}</li>"
                       for k, v in COVER_DESCRIPTIONS.items())
    season_label = f"{_esc(master['season'].replace('_', ' '))}"
    if season_year:
        season_label += f" {season_year}"
    receipt = (f"receipt {_esc(master['receipt_ref'])} ({_esc(master['receipt_date'])})"
               if master.get("receipt_ref") else "premium not yet receipted")

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Policy schedule — {_esc(master['id'])}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui, sans-serif; color: #0f1f33; max-width: 860px;
         margin: 2rem auto; padding: 0 1.5rem; line-height: 1.5; }}
  header {{ border-bottom: 3px solid #1d9bf0; padding-bottom: 1rem; margin-bottom: 1.25rem; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 0.25rem; }}
  h2 {{ font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.6px;
        color: #5f7089; margin: 1.75rem 0 0.6rem; }}
  h3 {{ font-size: 0.95rem; margin: 1.1rem 0 0.4rem; }}
  .sub {{ color: #5f7089; font-size: 0.9rem; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.86rem; margin-top: 0.3rem; }}
  th, td {{ border: 1px solid #e3eaf3; padding: 0.4rem 0.6rem; text-align: left; vertical-align: top; }}
  th {{ background: #f6f9fd; font-weight: 600; }}
  tr.off td {{ color: #93a2b8; }}
  td.mean {{ font-size: 0.8rem; color: #33465f; }}
  .kv {{ display: grid; grid-template-columns: 210px 1fr; gap: 0.35rem 1rem; font-size: 0.9rem; }}
  .kv dt {{ color: #5f7089; }}
  .kv dd {{ margin: 0; font-weight: 500; }}
  .badge {{ display: inline-block; font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
           letter-spacing: 0.4px; padding: 0.12rem 0.5rem; border-radius: 99px;
           background: #d9edfd; color: #0b6bcb; }}
  .legal {{ background: #fff8e6; border: 1px solid #f0d98c; border-radius: 8px;
           padding: 0.9rem 1.1rem; margin-top: 1.5rem; font-size: 0.83rem; }}
  .legal .ph {{ color: #92600e; font-style: italic; }}
  footer {{ margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #e3eaf3;
           color: #93a2b8; font-size: 0.78rem; }}
  ul {{ font-size: 0.86rem; }}
  ol.terms {{ font-size: 0.84rem; padding-left: 1.2rem; color: #24344a; }}
  ol.terms li {{ margin: 0.4rem 0; }}
  @media print {{ body {{ margin: 0; }} h2, h3 {{ break-after: avoid; }} }}
</style></head><body>
<header>
  <h1>Weather Index Insurance — Policy Schedule</h1>
  <div class="sub">Policy {_esc(master['id'])} · <span class="badge">{_esc(master['status'])}</span>
    · issued {_esc(master['created_at'][:10])}</div>
</header>

<h2>Policy summary</h2>
<dl class="kv">
  <dt>Product</dt><dd>{_esc(master['crop'])} · {season_label} · {_esc(master['country'])}</dd>
  <dt>Sale type</dt><dd>{_esc(master['sale_type'])}{f" — {_esc(master['partner_name'])}" if master.get('partner_name') else ''}</dd>
  <dt>Total sum insured</dt><dd>{_esc(f"{master['total_sum_insured']:,.0f}")}</dd>
  <dt>Total premium</dt><dd>{_esc(f"{master['total_premium']:,.0f}")} — {receipt}</dd>
  <dt>Season covered</dt><dd>{season_label}</dd>
</dl>

<h2>Insured</h2>
<table><thead><tr><th>Name</th><th>Phone</th><th>Zone</th><th>Sum insured</th><th>Premium</th></tr></thead>
<tbody>{sched_rows}</tbody></table>

<h2>Cover terms</h2>
{terms_html}

<h2>How a payout works</h2>
<ul>
  <li>The index is the <strong>area-average CHIRPS rainfall</strong> for your zone — the same rainfall figure for every farmer in the zone, measured from satellite and rain-gauge data. No field visit or claim is needed.</li>
  <li>Each stage pays as its trigger is breached, up to that stage's maximum, and payouts are settled on <strong>final CHIRPS data</strong> a few weeks after the season ends.</li>
  <li>There is <strong>one payment per farmer at the end of the season</strong>, paid through your existing channel.</li>
</ul>

<h2>What the cover types mean</h2>
<ul>{glossary}</ul>

<h2>Policy terms &amp; conditions</h2>
<div class="legal">
  <span class="ph">Generic pilot terms — draft for review. The clauses below are standard
  index-insurance terms provided as a starting point for the pilot. They must be reviewed,
  completed and approved by the insurer's counsel and regulator before commercial issue,
  and the bracketed items filled in. This is not legal advice.</span>
</div>
<ol class="terms">
  <li><strong>The contract.</strong> This schedule, together with these terms, forms the
    policy between the insured named above and the insurer. Cover applies only to the
    season, zone and stages shown in the schedule.</li>
  <li><strong>Nature of cover — index (parametric) insurance.</strong> This is index-based
    insurance. A payout is determined solely by a rainfall index measured against the
    triggers in the schedule — not by the insured's actual loss. No claim, field
    inspection, or proof of loss is required; payment is automatic when the index breaches
    a trigger.</li>
  <li><strong>The index.</strong> The index is the area-average CHIRPS rainfall for the
    insured's zone, measured for each covered stage from satellite and rain-gauge data
    published by the Climate Hazards Center. Settlement is made on final CHIRPS data. That
    data is the sole and conclusive basis for determining any payout.</li>
  <li><strong>How a payout is calculated.</strong> Each covered stage pays on a straight
    line from its strike (0%) to its exit (100% of that stage's limit). Stage payouts are
    added together and the total payable can never exceed the sum insured. One payment is
    made per insured after the season ends.</li>
  <li><strong>Basis risk — please read.</strong> Because payment follows the index and not
    the insured's own field, the payout may not match the actual loss. The policy may pay
    when little or no loss occurred, and may not pay when a loss did occur. The insured
    acknowledges and accepts this basis risk as a feature of index insurance.</li>
  <li><strong>Premium.</strong> Cover is conditional on the premium being paid in full. If
    the premium is not received, no cover is in force.</li>
  <li><strong>Period of cover.</strong> Cover applies only to the season stated in the
    schedule. Cover cannot be bought for a season that has already begun.</li>
  <li><strong>Payment of benefits.</strong> Any payout is made through the insured's
    recorded payment channel, after settlement on final data.</li>
  <li><strong>Personal data.</strong> The insured's personal data is held only to
    administer this policy and is processed in accordance with
    <span class="ph">[applicable data-protection law]</span>.</li>
  <li><strong>Fraud and misrepresentation.</strong> <span class="ph">[Insert the insurer's
    fraud and misrepresentation terms.]</span></li>
  <li><strong>Cancellation and cooling-off.</strong> <span class="ph">[Insert the
    cooling-off period and cancellation terms.]</span></li>
  <li><strong>Complaints and disputes.</strong> <span class="ph">[Insert the complaints
    procedure and dispute-resolution mechanism.]</span></li>
  <li><strong>Governing law.</strong> This policy is governed by the laws of
    <span class="ph">[jurisdiction]</span>.</li>
  <li><strong>The insurer.</strong> This cover is underwritten by
    <span class="ph">[licensed insurer legal name]</span>, licensed and regulated by
    <span class="ph">[regulator and licence number]</span>.</li>
</ol>

<footer>This document is a policy schedule generated by the platform. The figures above
reflect the cover recorded at binding. The terms are generic pilot wording and are not
legal advice; final terms must be issued by the insurer.</footer>
</body></html>"""


def policy_document_pdf(policy_id: str) -> bytes | None:
    """The policy schedule rendered to PDF (for emailing). None if no such policy.
    weasyprint is imported lazily so the rest of the app runs without it."""
    html = render_policy_document(policy_id)
    if html is None:
        return None
    from weasyprint import HTML

    return HTML(string=html).write_pdf()


def list_policies(created_by: str | None = None, *, partner: str | None = None,
                  product_id: str | None = None, status: str | None = None,
                  agent: str | None = None, zone: int | None = None) -> list[dict]:
    """Master-level register rows (no PII). `created_by` scopes to one agent;
    the filters make it navigable by partner, product, agent, status and zone."""
    sql = [f"SELECT {_MASTER_COLS} FROM master_policies m WHERE 1=1"]
    params: list = []
    if created_by is not None:
        sql.append("AND created_by = %s"); params.append(created_by)
    if agent:
        sql.append("AND created_by = %s"); params.append(agent)
    if partner:
        sql.append("AND partner_name ILIKE %s"); params.append(f"%{partner}%")
    if product_id:
        sql.append("AND product_id = %s"); params.append(product_id)
    if status:
        sql.append("AND status = %s"); params.append(status)
    if zone is not None:
        sql.append("AND EXISTS (SELECT 1 FROM policy_schedule s "
                   "WHERE s.master_policy_id = m.id AND s.zone = %s)")
        params.append(zone)
    sql.append("ORDER BY created_at DESC")
    with connect() as conn:
        rows = conn.execute(" ".join(sql), params).fetchall()
    out = []
    for r in rows:
        m = _master_row(r)
        with connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM policy_schedule WHERE master_policy_id=%s",
                             (m["id"],)).fetchone()[0]
        m["farmers"] = n
        out.append(m)
    return out
