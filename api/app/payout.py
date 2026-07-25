"""Payout run — season close: review, release, export the disbursement file.

Settlement (issue 016) produced the settled per-zone, per-phase payout on CHIRPS
final data. A payout run turns that into money owed to each farmer and out to the
payment rails:

  * per-farmer payout = the zone's settled payout rate (settled payout ÷ the
    product's sum insured) × that farmer's own sum insured — every farmer in a
    zone shares one index, so they share one rate; the amount scales with cover.
  * the run is reviewed first (totals, farmer count, largest amounts, per-zone
    table, and an anomaly flag on any zone paying more than 3× its priced
    expected loss), then one human clicks Release.
  * Release is a one-way lock: it writes an immutable run + one line per paid
    farmer, flips every active policy in the season to settled, and records an
    audit trail. Money is recorded here, never moved (SPEC §8) — the exported
    file is handed to the existing rails.

One payment per farmer: a farmer (one schedule row) appears at most once in the
file, enforced by a UNIQUE (run_id, schedule_id).
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from app.crypto import decrypt
from app.db import connect
from app.settlement import persisted_settlements, season_year_for

# A zone paying more than this multiple of its priced expected loss is flagged
# for a human to eyeball before release (SPEC §9 / user story 31).
ANOMALY_MULTIPLE = 3.0

# The exported payout file. Columns are fixed and documented here so the payment
# rails can parse it: one row per paid farmer, header first, amounts to 2 dp.
FILE_COLUMNS = ["policy_number", "phone", "zone", "amount", "evidence_reference"]


SCHEMA = """
CREATE TABLE IF NOT EXISTS payout_runs (
    id            TEXT PRIMARY KEY,
    product_id    TEXT NOT NULL REFERENCES published_products(id),
    season_year   INT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'released',
    farmer_count  INT NOT NULL,
    total_amount  DOUBLE PRECISION NOT NULL,
    zone_summary  JSONB NOT NULL,
    anomalies     JSONB NOT NULL,
    released_by   TEXT NOT NULL,
    released_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    audit         JSONB NOT NULL,
    -- One released run per product-season: a season is closed exactly once.
    UNIQUE (product_id, season_year)
);

CREATE TABLE IF NOT EXISTS payout_lines (
    id            SERIAL PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES payout_runs(id) ON DELETE CASCADE,
    schedule_id   INT NOT NULL,
    policy_id     TEXT NOT NULL,
    zone          INT NOT NULL,
    phone_enc     TEXT NOT NULL,
    amount        DOUBLE PRECISION NOT NULL,
    evidence_ref  TEXT NOT NULL,
    -- A farmer (schedule row) is paid exactly once in a run.
    UNIQUE (run_id, schedule_id)
);
"""


def init_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA)


class PayoutError(Exception):
    """A payout action was rejected for a reason the UI can show."""


def _run_id(product_id: str, season_year: int) -> str:
    return f"PR-{product_id}-{season_year}"


def evidence_ref(product_id: str, season_year: int, zone: int) -> str:
    """Stable pointer to a zone's settled index evidence (the settlements rows)."""
    return f"EV-{product_id}-{season_year}-Z{zone}"


# ----- the inputs: settled zone payouts + the active farmers in the season -----

def _active_schedule(product_id: str, season_year: int) -> list[dict]:
    """Active farmers bound to this product in this season, one row each.

    Only *active* policies are paid; draft/expired are excluded. Scoped to the
    season year the policies were bound in (see settlement.season_year_for)."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.id, s.master_policy_id, s.zone, s.sum_insured, s.phone_enc
               FROM policy_schedule s
               JOIN master_policies m ON m.id = s.master_policy_id
               WHERE m.product_id = %s
                 AND m.status = 'active'
                 AND EXTRACT(YEAR FROM m.created_at) = %s
               ORDER BY s.master_policy_id, s.id""",
            (product_id, season_year),
        ).fetchall()
    return [
        {"schedule_id": r[0], "policy_id": r[1], "zone": int(r[2]),
         "sum_insured": float(r[3]), "phone_enc": r[4]}
        for r in rows
    ]


def _zone_settled(product_id: str, season_year: int) -> dict[int, dict]:
    """Per-zone settled totals from the settlements table (issue 016).

    Returns {zone: {payout_total, phases: [names], phase_count}} over the settled
    rows only — the official, CHIRPS-final numbers."""
    settled = persisted_settlements(product_id, season_year)  # {(zone,phase): row}
    zones: dict[int, dict] = {}
    for (zone, phase), row in settled.items():
        z = zones.setdefault(zone, {"payout_total": 0.0, "phases": []})
        z["payout_total"] += row["payout"]
        z["phases"].append(phase)
    for z in zones.values():
        z["payout_total"] = round(z["payout_total"], 2)
        z["phase_count"] = len(z["phases"])
    return zones


# ----- readiness: the season only closes once every relevant phase is settled -----

def _readiness(product: dict, season_year: int, zones_with_policies: set[int]) -> dict:
    """A run is ready only when every phase of every zone that has policies is
    settled on final data. Returns {ready, pending: [{zone, settled, total}]}."""
    definition = product["definition"]
    settled = persisted_settlements(product["id"], season_year)
    settled_by_zone: dict[int, set[str]] = {}
    for (zone, phase) in settled:
        settled_by_zone.setdefault(zone, set()).add(phase)

    pending = []
    for zid in sorted(zones_with_policies):
        zdef = definition["zones"].get(str(zid)) or definition["zones"].get(zid) or {}
        phase_names = {p["name"] for p in zdef.get("phases", [])}
        have = settled_by_zone.get(zid, set())
        if not phase_names or not phase_names.issubset(have):
            pending.append({"zone": zid, "settled": len(have), "total": len(phase_names)})
    return {"ready": not pending, "pending": pending}


# ----- build the run for review (pure computation, nothing persisted) -----

def build_payout_run(product_id: str, season_year: int | None = None) -> dict:
    """Everything the operations manager reviews before releasing — computed
    live from the settled figures and the active schedule. Persists nothing."""
    from app.publish import get_published

    product = get_published(product_id)
    if not product:
        raise PayoutError(f"no published product {product_id}")
    season_year = season_year or season_year_for(product_id)

    schedule = _active_schedule(product_id, season_year)
    zone_settled = _zone_settled(product_id, season_year)
    sum_insured = product["sum_insured"]
    el_by_zone = {r["zone"]: r["expected_loss"] for r in product["rates"]}

    zones_with_policies = {f["zone"] for f in schedule}
    readiness = _readiness(product, season_year, zones_with_policies)

    # Per-farmer amounts: zone payout rate × the farmer's own sum insured.
    lines = []
    for f in schedule:
        zs = zone_settled.get(f["zone"])
        rate = (zs["payout_total"] / sum_insured) if (zs and sum_insured) else 0.0
        amount = round(rate * f["sum_insured"], 2)
        lines.append({
            "schedule_id": f["schedule_id"], "policy_id": f["policy_id"],
            "zone": f["zone"], "phone_enc": f["phone_enc"], "amount": amount,
            "evidence_ref": evidence_ref(product_id, season_year, f["zone"]),
        })
    paid = [ln for ln in lines if ln["amount"] > 0]

    # Per-zone review table with the >3× expected-loss anomaly flag.
    zone_table = []
    anomalies = []
    for zid in sorted(zones_with_policies):
        zs = zone_settled.get(zid, {"payout_total": 0.0, "phase_count": 0})
        el = el_by_zone.get(zid)
        n_farmers = sum(1 for f in schedule if f["zone"] == zid)
        zone_paid = round(sum(ln["amount"] for ln in paid if ln["zone"] == zid), 2)
        anomaly = bool(el and zs["payout_total"] > ANOMALY_MULTIPLE * el)
        row = {
            "zone": zid, "farmers": n_farmers,
            "settled_payout": zs["payout_total"],
            "expected_loss": el,
            "zone_payout": zone_paid,
            "phase_count": zs.get("phase_count", 0),
            "anomaly": anomaly,
            "evidence_ref": evidence_ref(product_id, season_year, zid),
        }
        zone_table.append(row)
        if anomaly:
            anomalies.append({
                "zone": zid, "settled_payout": zs["payout_total"],
                "expected_loss": el,
                "multiple": round(zs["payout_total"] / el, 2) if el else None,
            })

    total = round(sum(ln["amount"] for ln in paid), 2)
    largest = sorted(paid, key=lambda ln: ln["amount"], reverse=True)[:5]
    existing = get_run(_run_id(product_id, season_year))

    return {
        "run_id": _run_id(product_id, season_year),
        "product_id": product_id,
        "country": product["country"],
        "crop": product["crop"],
        "season": product["season"],
        "season_year": season_year,
        "sum_insured": sum_insured,
        "status": existing["status"] if existing else "draft",
        "released_at": existing["released_at"] if existing else None,
        "released_by": existing["released_by"] if existing else None,
        "ready": readiness["ready"],
        "pending": readiness["pending"],
        "farmer_count": len(paid),
        "total_farmers": len(schedule),
        "total_amount": total,
        "zones": zone_table,
        "anomalies": anomalies,
        "largest": [
            {"policy_id": ln["policy_id"], "zone": ln["zone"], "amount": ln["amount"]}
            for ln in largest
        ],
    }


# ----- release: the one-way lock that closes the season -----

def release_payout_run(product_id: str, season_year: int | None,
                       released_by: str) -> dict:
    """Freeze the run, write one line per paid farmer, flip every active policy
    in the season to settled, and record the audit. Refuses if not ready or
    already released — the season closes exactly once."""
    run = build_payout_run(product_id, season_year)
    season_year = run["season_year"]
    run_id = run["run_id"]

    if run["status"] == "released":
        raise PayoutError(f"payout run for {product_id} {season_year} is already released")
    if not run["ready"]:
        raise PayoutError(
            "not every phase is settled yet — the season is not ready to close")

    schedule = _active_schedule(product_id, season_year)
    zone_settled = _zone_settled(product_id, season_year)
    sum_insured = run["sum_insured"]

    paid_lines = []
    for f in schedule:
        zs = zone_settled.get(f["zone"])
        rate = (zs["payout_total"] / sum_insured) if (zs and sum_insured) else 0.0
        amount = round(rate * f["sum_insured"], 2)
        if amount > 0:
            paid_lines.append((f, amount))

    audit = {
        "action": "release_payout",
        "by": released_by,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "farmer_count": len(paid_lines),
        "total_amount": run["total_amount"],
        "anomalies": run["anomalies"],
    }

    master_ids = sorted({f["policy_id"] for f in schedule})

    with connect() as conn:
        with conn.transaction():
            conn.execute(
                """INSERT INTO payout_runs
                   (id, product_id, season_year, status, farmer_count, total_amount,
                    zone_summary, anomalies, released_by, audit)
                   VALUES (%s,%s,%s,'released',%s,%s,%s,%s,%s,%s)""",
                (run_id, product_id, season_year, len(paid_lines), run["total_amount"],
                 json.dumps(run["zones"]), json.dumps(run["anomalies"]),
                 released_by, json.dumps(audit)),
            )
            for f, amount in paid_lines:
                conn.execute(
                    """INSERT INTO payout_lines
                       (run_id, schedule_id, policy_id, zone, phone_enc, amount, evidence_ref)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (run_id, f["schedule_id"], f["policy_id"], f["zone"],
                     f["phone_enc"], amount,
                     evidence_ref(product_id, season_year, f["zone"])),
                )
            # Season close: every active policy becomes settled (paid or not).
            for mid in master_ids:
                conn.execute(
                    "UPDATE master_policies SET status='settled' "
                    "WHERE id=%s AND status='active'",
                    (mid,),
                )

    return get_run(run_id)


# ----- reads + the exported file -----

def get_run(run_id: str) -> dict | None:
    with connect() as conn:
        r = conn.execute(
            """SELECT id, product_id, season_year, status, farmer_count, total_amount,
                      zone_summary, anomalies, released_by, released_at, audit
               FROM payout_runs WHERE id = %s""",
            (run_id,),
        ).fetchone()
    if not r:
        return None
    return {
        "id": r[0], "product_id": r[1], "season_year": r[2], "status": r[3],
        "farmer_count": r[4], "total_amount": r[5], "zone_summary": r[6],
        "anomalies": r[7], "released_by": r[8],
        "released_at": r[9].isoformat() if r[9] else None, "audit": r[10],
    }


def _run_lines(run_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT policy_id, zone, phone_enc, amount, evidence_ref, schedule_id
               FROM payout_lines WHERE run_id = %s
               ORDER BY policy_id, schedule_id""",
            (run_id,),
        ).fetchall()
    return [
        {"policy_id": r[0], "zone": r[1], "phone_enc": r[2],
         "amount": r[3], "evidence_ref": r[4], "schedule_id": r[5]}
        for r in rows
    ]


def render_payout_file(run_id: str) -> str:
    """The disbursement CSV for the payment rails. Deterministic (rows ordered by
    policy then schedule id) so a released run always exports byte-for-byte the
    same file. Phone is decrypted here — the file leaves for the rails only."""
    run = get_run(run_id)
    if not run:
        raise PayoutError(f"no payout run {run_id}")
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(FILE_COLUMNS)
    for ln in _run_lines(run_id):
        writer.writerow([
            ln["policy_id"], decrypt(ln["phone_enc"]) or "", ln["zone"],
            f"{ln['amount']:.2f}", ln["evidence_ref"],
        ])
    return buf.getvalue()


def list_runs(product_id: str | None = None) -> list[dict]:
    sql = ["SELECT id FROM payout_runs"]
    params: list = []
    if product_id:
        sql.append("WHERE product_id = %s"); params.append(product_id)
    sql.append("ORDER BY released_at DESC")
    with connect() as conn:
        rows = conn.execute(" ".join(sql), params).fetchall()
    return [get_run(r[0]) for r in rows]
