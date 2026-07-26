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
                        premium_rate, premium, name_enc, phone_enc, national_id_enc, gender)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (policy_id, r["quote_reference"], r["zone"], r["sum_insured"],
                     r["premium_rate"], r["premium"], r["name_enc"], r["phone_enc"],
                     r["national_id_enc"], r["gender"]))

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
                      name_enc, phone_enc, national_id_enc, gender
               FROM policy_schedule WHERE master_policy_id=%s ORDER BY id""",
            (policy_id,)).fetchall()
    master["schedule"] = [
        {
            "id": s[0], "quote_reference": s[1], "zone": s[2], "sum_insured": s[3],
            "premium_rate": s[4], "premium": s[5],
            "farmer": {"name": decrypt(s[6]), "phone": decrypt(s[7]),
                       "national_id": decrypt(s[8]), "gender": s[9]},
        }
        for s in rows
    ]
    return master


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
