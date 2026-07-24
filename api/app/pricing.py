"""Pricing Engine — turn a zone's historical payouts into a commercial premium.

Two views of the pure risk cost (SPEC.md §6):
  - burning cost: the plain average of historical payouts
  - modelled expected loss: a frequency-severity model — how often a payout
    happens (empirical) times a distribution fitted to the payout sizes —
    simulated to smooth out a short, lumpy history
Technical expected loss = max(burning cost, modelled) — the prudent Twister
convention.

Then loadings (uncertainty margin, admin, distribution, profit ...) are added
to reach the gross premium. Loadings come in three bases and %-of-gross ones
compound, so the gross-up is: gross = base / (1 - sum of %-of-gross fractions).
"""

from __future__ import annotations

import numpy as np
from scipy import stats

DISTRIBUTIONS = ("gamma", "lognormal", "normal")

# Recommended starting loadings — every value is editable by the actuary.
DEFAULT_LOADINGS = [
    {"name": "Uncertainty margin", "basis": "pct_el", "value": 15.0},
    {"name": "Admin & operations", "basis": "pct_gross", "value": 10.0},
    {"name": "Distribution commission", "basis": "pct_gross", "value": 15.0},
    {"name": "Profit & capital", "basis": "pct_gross", "value": 10.0},
]

BASES = ("pct_el", "pct_gross", "flat")


def _fit(dist: str, positive: np.ndarray):
    """Fit a severity distribution to strictly-positive payouts."""
    if dist == "gamma":
        a, loc, scale = stats.gamma.fit(positive, floc=0)
        return stats.gamma(a, loc=loc, scale=scale), {"shape": a, "scale": scale}
    if dist == "lognormal":
        s, loc, scale = stats.lognorm.fit(positive, floc=0)
        return stats.lognorm(s, loc=loc, scale=scale), {"sigma": s, "scale": scale}
    if dist == "normal":
        mu, sigma = stats.norm.fit(positive)
        return stats.norm(loc=mu, scale=sigma), {"mean": mu, "sd": sigma}
    raise ValueError(f"unknown distribution: {dist}")


def expected_loss(
    losses: list[float],
    sum_insured: float,
    dist: str = "gamma",
    n_sim: int = 20000,
    seed: int = 1,
) -> dict:
    """Burning cost, modelled EL, technical EL and a Q-Q plot for one zone."""
    arr = np.asarray(losses, dtype=float)
    burning_cost = float(arr.mean()) if len(arr) else 0.0
    positive = arr[arr > 0]
    freq = float((arr > 0).mean()) if len(arr) else 0.0

    fit_params = None
    qq = []
    if len(positive) >= 2 and dist:
        frozen, fit_params = _fit(dist, positive)
        # Frequency-severity Monte Carlo, clipped to the cover limit.
        rng = np.random.default_rng(seed)
        occur = rng.random(n_sim) < freq
        sev = np.clip(frozen.rvs(size=n_sim, random_state=rng), 0, sum_insured)
        sims = np.where(occur, sev, 0.0)
        modelled_el = float(sims.mean())
        # Q-Q: sorted positive losses vs fitted quantiles at plotting positions.
        s = np.sort(positive)
        pp = (np.arange(1, len(s) + 1) - 0.5) / len(s)
        theo = frozen.ppf(pp)
        qq = [{"actual": float(a), "theoretical": float(t)} for a, t in zip(s, theo)]
    else:
        # Too few positive years to fit — fall back to burning cost.
        modelled_el = burning_cost

    technical_el = max(burning_cost, modelled_el)
    return {
        "burning_cost": round(burning_cost, 2),
        "frequency": round(freq, 3),
        "modelled_el": round(modelled_el, 2),
        "technical_el": round(technical_el, 2),
        "distribution": dist,
        "fit_params": fit_params,
        "qq": qq,
        "n_years": len(arr),
    }


def apply_loadings(expected_loss_value: float, loadings: list[dict], sum_insured: float) -> dict:
    """Build the gross premium from the technical EL and the loading list.

    base       = EL + (%-of-EL loadings) + (flat loadings)
    gross      = base / (1 - sum of %-of-gross fractions)
    """
    el = float(expected_loss_value)
    base = el
    breakdown = []
    for ld in loadings:
        if ld["basis"] == "pct_el":
            amt = el * ld["value"] / 100.0
            base += amt
            breakdown.append({**ld, "amount": round(amt, 2)})
        elif ld["basis"] == "flat":
            base += ld["value"]
            breakdown.append({**ld, "amount": round(float(ld["value"]), 2)})

    g = sum(ld["value"] for ld in loadings if ld["basis"] == "pct_gross") / 100.0
    if g >= 1.0:
        raise ValueError("percent-of-gross loadings sum to 100% or more — premium is undefined")
    gross = base / (1.0 - g)

    # Now the %-of-gross amounts are known.
    for ld in loadings:
        if ld["basis"] == "pct_gross":
            breakdown.append({**ld, "amount": round(gross * ld["value"] / 100.0, 2)})

    return {
        "expected_loss": round(el, 2),
        "gross_premium": round(gross, 2),
        "premium_rate": round(100.0 * gross / sum_insured, 2) if sum_insured else 0.0,
        "loading_breakdown": breakdown,
    }
