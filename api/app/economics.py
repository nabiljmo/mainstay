"""Per-zone economics: payout history + expected loss + loadings -> rate.

This is the single computation shared by the live pricing endpoint (one zone,
interactive) and the publish flow (every zone, frozen). Keeping it in one place
guarantees a published rate is byte-for-byte the number the actuary saw on
screen when they hit publish.
"""

from __future__ import annotations

from app.explain import (
    explain_burning_cost,
    explain_expected_loss,
    explain_loading,
    explain_payout,
    explain_phase,
    explain_premium,
    explain_year,
)
from app.index_engine import phase_from_dict
from app.pricing import DEFAULT_LOADINGS, apply_loadings, expected_loss
from app.products import historical_table
from app.zoning import quality_flag


def compute_zone_economics(
    store,
    country: str,
    zone_geojson: dict,
    years: list[int],
    plant_start: str,
    sum_insured: float,
    zone: int,
    phases: list[dict],
    *,
    distribution: str = "gamma",
    loadings: list[dict] | None = None,
    cache_key: str | None = None,
    explanations: bool = True,
) -> dict:
    """Full pricing for one zone. Raises ValueError on invalid trigger terms
    (e.g. a strike edited past its exit) — callers turn that into a 400.

    `explanations=False` skips the plain-words text (used when publishing many
    zones, where only the numbers are frozen into the rate table).
    """
    table = historical_table(
        store, country, years, zone_geojson, zone, phases, plant_start, cache_key=cache_key,
    )
    losses = [r["total_payout"] for r in table]

    resolved = {}
    phase_meanings = []
    for p in phases:
        rp = phase_from_dict({**p, "trigger_mode": p.get("trigger_mode", "absolute")})
        resolved[rp.name] = rp
        if explanations:
            phase_meanings.append(
                {"name": rp.name, "meaning": explain_phase(
                    rp.name, rp.cover_type, p.get("reference"), rp.strike, rp.exit_, rp.limit)}
            )

    if explanations:
        for yr in table:
            for ph in yr["phases"]:
                rp = resolved[ph["phase"]]
                ph["why"] = explain_payout(
                    yr["year"], rp.name, rp.cover_type, ph["index"],
                    rp.strike, rp.exit_, ph["limit"], ph["payout"])
            yr["summary"] = explain_year(yr["year"], yr["phases"], sum_insured)

    econ = expected_loss(losses, sum_insured, dist=distribution)
    loadings = loadings if loadings is not None else DEFAULT_LOADINGS
    price = apply_loadings(econ["technical_el"], loadings, sum_insured)

    result = {
        "zone": zone,
        "sum_insured": sum_insured,
        "quality_flag": quality_flag(len(years)),
        "years": table,
        "burning_cost": econ["burning_cost"],
        "phase_meanings": phase_meanings,
        "economics": econ,
        "price": price,
    }
    if explanations:
        result["burning_cost_explanation"] = explain_burning_cost(
            econ["burning_cost"], sum_insured, len(table))
        result["explanations"] = {
            "expected_loss": explain_expected_loss(econ, sum_insured),
            "premium": explain_premium(econ, price, sum_insured),
            "loadings": [explain_loading(b, sum_insured) for b in price["loading_breakdown"]],
        }
    return result
