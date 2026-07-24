import numpy as np
import pytest

from app.index_engine import (
    DEFICIT,
    DRY_SPELL,
    EXCESS,
    Phase,
    apportion_limits,
    longest_dry_run,
    payout_fraction,
    phase_from_dict,
    phase_index,
    phase_payout,
    phase_windows,
    propose_triggers,
    run_year,
)

# ---------------- index computation (golden) ----------------

def test_deficit_and_excess_index_is_total_rainfall():
    days = np.array([5.0, 0.0, 10.0, 2.5, 0.0])
    assert phase_index(days, DEFICIT) == 17.5
    assert phase_index(days, EXCESS) == 17.5


def test_nan_days_ignored_in_totals():
    days = np.array([5.0, np.nan, 10.0])
    assert phase_index(days, DEFICIT) == 15.0


def test_longest_dry_run_basic():
    # threshold 2mm: dry days are <2. Runs: [1,1,1]=3, then broken, then [1,1]=2
    rain = np.array([0.0, 1.0, 1.5, 5.0, 0.0, 1.0, 9.0])
    assert longest_dry_run(rain, 2.0) == 3


def test_dry_run_broken_by_nan():
    rain = np.array([0.0, 0.0, np.nan, 0.0, 0.0])
    assert longest_dry_run(rain, 2.0) == 2


def test_dry_spell_index():
    rain = np.array([0.0, 0.0, 0.0, 5.0, 0.0])
    assert phase_index(rain, DRY_SPELL, dry_threshold=2.0) == 3.0


# ---------------- payout fraction (golden + boundary) ----------------

def test_deficit_payout_linear():
    # strike 100 (0%), exit 40 (100%). index 70 => halfway => 0.5
    assert payout_fraction(70, DEFICIT, strike=100, exit_=40) == pytest.approx(0.5)


def test_deficit_boundaries_exact():
    assert payout_fraction(100, DEFICIT, 100, 40) == 0.0   # at strike
    assert payout_fraction(40, DEFICIT, 100, 40) == 1.0    # at exit
    assert payout_fraction(120, DEFICIT, 100, 40) == 0.0   # above strike, no payout
    assert payout_fraction(10, DEFICIT, 100, 40) == 1.0    # below exit, capped


def test_excess_payout_linear_and_boundaries():
    assert payout_fraction(150, EXCESS, strike=100, exit_=200) == pytest.approx(0.5)
    assert payout_fraction(100, EXCESS, 100, 200) == 0.0
    assert payout_fraction(200, EXCESS, 100, 200) == 1.0
    assert payout_fraction(50, EXCESS, 100, 200) == 0.0


def test_dry_spell_payout():
    # strike 7 dry days (0%), exit 14 (100%). 10 days => 3/7
    assert payout_fraction(10, DRY_SPELL, strike=7, exit_=14) == pytest.approx(3 / 7)


def test_bad_trigger_ordering_rejected():
    with pytest.raises(ValueError):
        payout_fraction(50, DEFICIT, strike=40, exit_=100)  # strike must be > exit
    with pytest.raises(ValueError):
        payout_fraction(50, EXCESS, strike=100, exit_=40)   # exit must be > strike


# ---------------- property tests ----------------

def test_deficit_payout_monotonic_in_deficit():
    ph = Phase("f", DEFICIT, 0, 10, strike=100, exit_=40, limit=1000)
    payouts = [phase_payout(idx, ph) for idx in range(0, 140, 10)]
    # As rainfall (index) rises, deficit payout must not increase.
    assert payouts == sorted(payouts, reverse=True)


def test_payout_never_exceeds_limit():
    ph = Phase("f", DEFICIT, 0, 10, strike=100, exit_=40, limit=1000)
    for idx in range(-50, 200, 5):
        p = phase_payout(idx, ph)
        assert 0.0 <= p <= 1000.0


def test_excess_payout_monotonic_increasing():
    ph = Phase("h", EXCESS, 0, 10, strike=100, exit_=200, limit=500)
    payouts = [phase_payout(idx, ph) for idx in range(0, 260, 10)]
    assert payouts == sorted(payouts)


# ---------------- phase windows & limits ----------------

def test_phase_windows_cumulative_offsets():
    stages = [
        {"name": "est", "days": 20, "sensitivity": 0.15},
        {"name": "veg", "days": 35, "sensitivity": 0.20},
        {"name": "flo", "days": 25, "sensitivity": 0.40},
    ]
    w = phase_windows(stages)
    assert w[0] == ("est", 0, 20, 0.15)
    assert w[1] == ("veg", 20, 55, 0.20)
    assert w[2] == ("flo", 55, 80, 0.40)


def test_apportion_limits_by_sensitivity():
    stages = [
        {"name": "a", "sensitivity": 0.15},
        {"name": "b", "sensitivity": 0.20},
        {"name": "c", "sensitivity": 0.40},
        {"name": "d", "sensitivity": 0.25},
    ]
    limits = apportion_limits(stages, 10000)
    assert sum(limits) == pytest.approx(10000)
    assert limits[2] == pytest.approx(4000)  # flowering gets the most


def test_apportion_handles_zero_weights():
    stages = [{"name": "a", "sensitivity": 0}, {"name": "b", "sensitivity": 0}]
    limits = apportion_limits(stages, 1000)
    assert limits == pytest.approx([500, 500])


# ---------------- trigger proposal ----------------

def test_deficit_triggers_from_low_tail():
    history = list(range(50, 150))  # 50..149
    strike, exit_ = propose_triggers(history, DEFICIT)
    assert strike > exit_            # put ordering
    assert exit_ < np.percentile(history, 10)


def test_excess_triggers_from_high_tail():
    history = list(range(50, 150))
    strike, exit_ = propose_triggers(history, EXCESS)
    assert exit_ > strike
    assert strike > np.percentile(history, 50)


# ---------------- percentage triggers ----------------

def test_percent_triggers_resolve_against_reference():
    # deficit: strike 80% of a 100mm normal => 80mm; exit 40% => 40mm.
    p = {
        "name": "flo", "cover_type": DEFICIT, "start_offset": 0, "end_offset": 10,
        "reference": 100.0, "strike_pct": 80, "exit_pct": 40,
        "trigger_mode": "percent", "limit": 1000,
    }
    ph = phase_from_dict(p)
    assert ph.strike == pytest.approx(80.0)
    assert ph.exit_ == pytest.approx(40.0)
    # index of 60mm (60% of normal) sits halfway between 80 and 40 => 0.5 payout
    assert phase_payout(60, ph) == pytest.approx(500.0)


def test_absolute_mode_ignores_percentages():
    p = {
        "name": "veg", "cover_type": DEFICIT, "start_offset": 0, "end_offset": 10,
        "reference": 100.0, "strike": 90, "exit": 40,
        "strike_pct": 999, "exit_pct": 999,
        "trigger_mode": "absolute", "limit": 1000,
    }
    ph = phase_from_dict(p)
    assert ph.strike == 90 and ph.exit_ == 40


def test_percent_falls_back_to_absolute_without_reference():
    # grain-fill in a bone-dry zone: no meaningful normal, so absolute is used.
    p = {
        "name": "grain", "cover_type": DEFICIT, "start_offset": 0, "end_offset": 10,
        "reference": 0, "strike": 5, "exit": 1,
        "strike_pct": None, "exit_pct": None,
        "trigger_mode": "percent", "limit": 1000,
    }
    ph = phase_from_dict(p)
    assert ph.strike == 5 and ph.exit_ == 1


# ---------------- whole-year run ----------------

def test_run_year_slices_phases_correctly():
    # 100-day series; plant at day 10. Two phases: days 0-5 and 5-10 after planting.
    daily = np.zeros(100)
    daily[12] = 30.0   # inside phase 1 (plant+2)
    daily[17] = 40.0   # inside phase 2 (plant+7)
    phases = [
        Phase("p1", DEFICIT, 0, 5, strike=100, exit_=0, limit=500),
        Phase("p2", DEFICIT, 5, 10, strike=100, exit_=0, limit=500),
    ]
    result = run_year(daily, plant_index=10, phases=phases)
    assert result[0]["index"] == 30.0
    assert result[1]["index"] == 40.0
    # both below strike 100 => partial payouts, both positive
    assert result[0]["payout"] > 0 and result[1]["payout"] > 0
