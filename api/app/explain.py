"""Auto-explainer — turn each calculation into a plain sentence a non-technical
reader can follow. Pure functions, reused across pricing, settlement and quotes
so the words the actuary sees are the same words a farmer or partner would get.
"""

from __future__ import annotations

from app.index_engine import DEFICIT, DRY_SPELL, EXCESS, payout_fraction


def _unit(cover_type: str) -> str:
    return "days" if cover_type == DRY_SPELL else "mm"


def _pct_of_normal(value: float, reference: float | None) -> int | None:
    if not reference:
        return None
    return round(100 * value / reference)


def _money(x: float) -> str:
    return f"{round(x):,}"


def explain_phase(
    name: str,
    cover_type: str,
    reference: float | None,
    strike: float,
    exit_: float,
    limit: float,
) -> str:
    """What one phase's settings mean, in plain words."""
    unit = _unit(cover_type)
    label = name.replace("_", " ")
    sp = _pct_of_normal(strike, reference)
    ep = _pct_of_normal(exit_, reference)
    sp_txt = f" ({sp}% of normal)" if sp is not None else ""
    ep_txt = f" ({ep}% of normal)" if ep is not None else ""

    if cover_type == DEFICIT:
        head = f"During the {label} stage, this cover protects against drought — too little rain."
        normal = (
            f" A normal year brings about {round(reference)} {unit} of rain then."
            if reference else ""
        )
        body = (
            f" Payments begin once rain falls below {round(strike)} {unit}{sp_txt},"
            f" and reach the full {_money(limit)} once it drops to {round(exit_)} {unit}{ep_txt} or less."
        )
    elif cover_type == EXCESS:
        head = f"During the {label} stage, this cover protects against too much rain."
        normal = (
            f" A normal year brings about {round(reference)} {unit} of rain then."
            if reference else ""
        )
        body = (
            f" Payments begin once rain rises above {round(strike)} {unit}{sp_txt},"
            f" and reach the full {_money(limit)} once it climbs to {round(exit_)} {unit}{ep_txt} or more."
        )
    else:  # DRY_SPELL
        head = f"During the {label} stage, this cover protects against long dry spells."
        normal = (
            f" In a normal year the longest dry spell here is about {round(reference)} days."
            if reference else ""
        )
        body = (
            f" Payments begin once the longest dry spell passes {round(strike)} days{sp_txt},"
            f" and reach the full {_money(limit)} at {round(exit_)} days{ep_txt} or more."
        )
    return head + normal + body + " In between, the payment grows steadily."


def explain_payout(
    year: int,
    name: str,
    cover_type: str,
    index: float,
    strike: float,
    exit_: float,
    limit: float,
    payout: float,
) -> str:
    """Why one phase paid what it paid, in one year."""
    unit = _unit(cover_type)
    idx = round(index)
    measure = (
        f"the longest dry spell was {idx} days"
        if cover_type == DRY_SPELL
        else f"rain was {idx} {unit}"
    )
    if payout <= 0:
        if cover_type == DEFICIT:
            reason = f"comfortably above the {round(strike)} {unit} worry line"
        elif cover_type == EXCESS:
            reason = f"below the {round(strike)} {unit} worry line"
        else:
            reason = f"under the {round(strike)}-day worry line"
        return f"{year}: {measure} — {reason} — so no payment was due."
    frac = payout_fraction(index, cover_type, strike, exit_)
    return (
        f"{year}: {measure} — bad enough to trigger the {name.replace('_', ' ')} cover — "
        f"so it paid {round(frac * 100)}% of this stage's pot, {_money(payout)} of {_money(limit)}."
    )


def explain_year(year: int, phases: list[dict], sum_insured: float) -> str:
    """One-line summary of a whole year across all phases."""
    paid = [p for p in phases if p["payout"] > 0]
    total = sum(p["payout"] for p in phases)
    if total <= 0:
        return f"{year}: a good enough season — nothing triggered, so no payout."
    if total >= sum_insured * 0.999:
        return (
            f"{year}: a severe season — every stage that could trigger did — "
            f"paying out the full {_money(sum_insured)}."
        )
    names = ", ".join(p["phase"].replace("_", " ") for p in paid)
    plural = "s" if len(paid) > 1 else ""
    return (
        f"{year}: the {names} stage{plural} triggered, paying {_money(total)} "
        f"of {_money(sum_insured)} ({round(100 * total / sum_insured)}%)."
    )


def explain_expected_loss(econ: dict, sum_insured: float) -> str:
    """What the expected-loss figures mean and why one was chosen."""
    bc = econ["burning_cost"]
    model = econ["modelled_el"]
    tech = econ["technical_el"]
    freq = round(econ["frequency"] * 100)
    chosen = "the burning cost" if tech == bc else "the modelled figure"
    return (
        f"On average a payout happened in about {freq}% of years. The plain average payout "
        f"(the 'burning cost') is {_money(bc)}. A fitted model, which smooths out such a short "
        f"history, gives {_money(model)}. We take the higher of the two — {_money(tech)}, "
        f"{chosen} — as the expected loss, the amount we'd expect to pay each year. That's the "
        f"starting point for the price, before any costs or margin."
    )


def explain_premium(econ: dict, price: dict, sum_insured: float) -> str:
    """How the expected loss became the final premium."""
    el = price["expected_loss"]
    gross = price["gross_premium"]
    rate = price["premium_rate"]
    added = gross - el
    return (
        f"We start from the {_money(el)} expected loss. On top we add {_money(added)} of "
        f"loadings — the running costs, sales commission and a margin for safety and profit — "
        f"to reach a premium of {_money(gross)}. For {_money(sum_insured)} of cover that is a "
        f"rate of {rate}% — so a farmer insuring {_money(sum_insured)} would pay about "
        f"{_money(gross)} for the season."
    )


def explain_loading(loading: dict, sum_insured: float) -> str:
    """One loading line, in plain words."""
    name = loading["name"]
    amt = loading.get("amount", 0)
    if loading["basis"] == "pct_el":
        how = f"{loading['value']}% of the expected loss"
    elif loading["basis"] == "pct_gross":
        how = f"{loading['value']}% of the final premium"
    else:
        how = "a fixed amount per policy"
    return f"{name}: {how}, adding {_money(amt)}."


def explain_burning_cost(burning_cost: float, sum_insured: float, n_years: int) -> str:
    """What the burning cost figure means."""
    pct = round(100 * burning_cost / sum_insured, 1) if sum_insured else 0
    return (
        f"Over these {n_years} years the cover would have paid {_money(burning_cost)} a year "
        f"on average — that's {pct}% of the {_money(sum_insured)} insured. This average is the "
        f"'burning cost': the bare cost of the risk itself, before any running costs or margin "
        f"are added to reach a price."
    )
