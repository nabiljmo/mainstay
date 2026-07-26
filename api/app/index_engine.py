"""Index Engine — the heart of the platform.

Pure computation: a product definition plus a daily rainfall series in, and
per-phase index values and payouts out. Deliberately date-agnostic — the same
functions price on historical data and settle on a live season, so what is
priced can never disagree with what pays out.

Three cover types (SPEC.md §5), all with linear strike->exit payouts:
  - deficit  (put)  : index = total phase rainfall; pays as it falls BELOW strike
  - excess   (call) : index = total phase rainfall; pays as it rises ABOVE strike
  - dry_spell(call) : index = longest run of consecutive dry days; pays as it
                      rises ABOVE strike

Phase start is a swappable rule. v1 uses a fixed calendar (planting date +
cumulative stage durations); the interface takes phase windows as day-offsets
so v2 can compute dynamic rainfall-triggered onset without touching this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

DEFICIT = "deficit"
EXCESS = "excess"
DRY_SPELL = "dry_spell"
COVER_TYPES = (DEFICIT, EXCESS, DRY_SPELL)

# A day is "dry" (for dry-spell cover) below this many mm.
DEFAULT_DRY_THRESHOLD = 2.0


@dataclass
class Phase:
    name: str
    cover_type: str
    start_offset: int           # days after planting (inclusive)
    end_offset: int             # days after planting (exclusive)
    strike: float
    exit_: float
    limit: float                # money at risk in this phase
    dry_threshold: float = DEFAULT_DRY_THRESHOLD

    def __post_init__(self):
        if self.cover_type not in COVER_TYPES:
            raise ValueError(f"unknown cover type: {self.cover_type}")


def phase_from_dict(p: dict) -> Phase:
    """Build a Phase from a stored/edited definition, resolving percentage
    triggers to absolute index units.

    Triggers may be given two ways:
      - absolute: strike/exit in the index's own units (mm, or dry days)
      - percent : strike_pct/exit_pct as a % of the phase's `reference`
                  (its historical mean index — the "normal"). e.g. a deficit
                  strike of 80% pays once rainfall falls below 80% of normal.
    """
    mode = p.get("trigger_mode", "absolute")
    ref = p.get("reference")
    if mode == "percent" and ref:
        strike = p["strike_pct"] / 100.0 * ref
        exit_ = p["exit_pct"] / 100.0 * ref
    else:
        strike = p["strike"]
        exit_ = p.get("exit_", p.get("exit"))
    return Phase(
        name=p["name"],
        cover_type=p["cover_type"],
        start_offset=p["start_offset"],
        end_offset=p["end_offset"],
        strike=strike,
        exit_=exit_,
        limit=p["limit"],
        dry_threshold=p.get("dry_threshold", DEFAULT_DRY_THRESHOLD),
    )


# ----- phase window construction (the swappable "fixed calendar" rule) -----

def phase_windows(stages: list[dict]) -> list[tuple[str, int, int, float]]:
    """Turn crop stages into (name, start_offset, end_offset, sensitivity)
    day-offset windows, measured from the planting date."""
    windows = []
    cursor = 0
    for s in stages:
        start = cursor
        end = cursor + int(s["days"])
        windows.append((s["name"], start, end, float(s.get("sensitivity", 0.0))))
        cursor = end
    return windows


def apportion_limits(stages: list[dict], sum_insured: float) -> list[float]:
    """Split the sum insured across stages by water-stress sensitivity
    (normalised, so weights need not sum to exactly 1)."""
    weights = np.array([float(s.get("sensitivity", 0.0)) for s in stages], dtype=float)
    total = weights.sum()
    if total <= 0:
        weights = np.ones_like(weights)
        total = weights.sum()
    return (weights / total * sum_insured).tolist()


# ----- index computation -----

def longest_dry_run(daily_mm: np.ndarray, threshold: float) -> int:
    """Longest run of consecutive days strictly below `threshold`.
    NaN days break a run (missing data is not evidence of dryness)."""
    best = run = 0
    for v in daily_mm:
        if not np.isnan(v) and v < threshold:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def phase_index(daily_mm: np.ndarray, cover_type: str, dry_threshold: float = DEFAULT_DRY_THRESHOLD) -> float:
    """The index value for one phase's daily rainfall slice."""
    if cover_type in (DEFICIT, EXCESS):
        return float(np.nansum(daily_mm))
    if cover_type == DRY_SPELL:
        return float(longest_dry_run(daily_mm, dry_threshold))
    raise ValueError(f"unknown cover type: {cover_type}")


# ----- payout -----

def payout_fraction(index: float, cover_type: str, strike: float, exit_: float) -> float:
    """Fraction of the phase limit due, linear between strike (0%) and exit (100%)."""
    if cover_type == DEFICIT:
        # put: strike > exit; drier => bigger payout
        if strike <= exit_:
            raise ValueError("deficit cover requires strike > exit")
        frac = (strike - index) / (strike - exit_)
    else:
        # call (excess or dry_spell): exit > strike; higher index => bigger payout
        if exit_ <= strike:
            raise ValueError(f"{cover_type} cover requires exit > strike")
        frac = (index - strike) / (exit_ - strike)
    return float(min(1.0, max(0.0, frac)))


def phase_payout(index: float, phase: Phase) -> float:
    return payout_fraction(index, phase.cover_type, phase.strike, phase.exit_) * phase.limit


# ----- trigger proposal (percentile-based starting points) -----

def propose_triggers(history: list[float], cover_type: str) -> tuple[float, float]:
    """Starting strike/exit from the historical index distribution.

    Deficit protects the dry tail (low percentiles); excess/dry_spell protect
    the high tail. The strike is set so the cover fires in roughly 1-2 years in
    10 (deficit strike at the 15th percentile, excess/dry at the 85th), not 3 in
    10 — a payout that rare keeps the premium affordable. Full payout is reached
    only in a genuinely extreme year (5th / 95th). These are proposals — the
    actuary always overrides."""
    arr = np.asarray(history, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) == 0:
        return (0.0, 0.0)
    if cover_type == DEFICIT:
        strike = float(np.percentile(arr, 15))
        exit_ = float(np.percentile(arr, 5))
        if strike <= exit_:               # near-degenerate history
            strike = exit_ + 1.0
    else:
        strike = float(np.percentile(arr, 85))
        exit_ = float(np.percentile(arr, 95))
        if exit_ <= strike:
            exit_ = strike + 1.0
    return (round(strike, 2), round(exit_, 2))


def default_cover_for(stage_name: str) -> str:
    """Sensible default cover per stage: dry-spell where a mid-season break
    kills the crop (establishment, flowering), deficit elsewhere."""
    if stage_name in ("establishment", "flowering"):
        return DRY_SPELL
    return DEFICIT


# ----- running a whole product over a season / history -----

def slice_phase(daily_mm: np.ndarray, plant_index: int, phase: Phase) -> np.ndarray:
    """Extract a phase's daily slice from a year's series, given the index of
    the planting day within that series."""
    start = plant_index + phase.start_offset
    end = plant_index + phase.end_offset
    return daily_mm[start:end]


def run_year(daily_mm: np.ndarray, plant_index: int, phases: list[Phase]) -> list[dict]:
    """Index and payout for each phase in a single year's daily series."""
    out = []
    for ph in phases:
        sl = slice_phase(daily_mm, plant_index, ph)
        idx = phase_index(sl, ph.cover_type, ph.dry_threshold)
        out.append(
            {
                "phase": ph.name,
                "cover_type": ph.cover_type,
                "index": idx,
                "payout": phase_payout(idx, ph),
                "limit": ph.limit,
            }
        )
    return out


def planting_day_of_year(plant_start: str, year: int) -> int:
    """Day-of-year (0-based) for a 'MM-DD' planting date in a given year."""
    month, day = (int(x) for x in plant_start.split("-"))
    d = date(year, month, day)
    return (d - date(year, 1, 1)).days
