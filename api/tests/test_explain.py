from app.explain import (
    explain_burning_cost,
    explain_payout,
    explain_phase,
    explain_year,
)
from app.index_engine import DEFICIT, DRY_SPELL, EXCESS


def test_deficit_phase_explanation_mentions_key_numbers():
    txt = explain_phase("flowering", DEFICIT, reference=100, strike=80, exit_=40, limit=4000)
    assert "drought" in txt
    assert "80 mm" in txt and "80% of normal" in txt
    assert "40 mm" in txt and "40% of normal" in txt
    assert "4,000" in txt


def test_excess_phase_explanation_direction():
    txt = explain_phase("harvest", EXCESS, reference=100, strike=120, exit_=180, limit=2000)
    assert "too much rain" in txt
    assert "rises above" in txt


def test_dry_spell_uses_days():
    txt = explain_phase("establishment", DRY_SPELL, reference=8, strike=10, exit_=15, limit=1500)
    assert "dry spell" in txt
    assert "days" in txt


def test_payout_explanation_when_paid():
    txt = explain_payout(2022, "flowering", DEFICIT, index=60, strike=80, exit_=40, limit=4000, payout=2000)
    assert "60 mm" in txt
    assert "50%" in txt
    assert "2,000 of 4,000" in txt


def test_payout_explanation_when_nothing_due():
    txt = explain_payout(2023, "flowering", DEFICIT, index=95, strike=80, exit_=40, limit=4000, payout=0)
    assert "no payment" in txt
    assert "95 mm" in txt


def test_year_summary_full_loss():
    phases = [{"phase": "a", "payout": 6000}, {"phase": "b", "payout": 4000}]
    txt = explain_year(2021, phases, sum_insured=10000)
    assert "severe" in txt and "10,000" in txt


def test_year_summary_partial():
    phases = [{"phase": "flowering", "payout": 2500}, {"phase": "vegetative", "payout": 0}]
    txt = explain_year(2023, phases, sum_insured=10000)
    assert "flowering" in txt
    assert "2,500" in txt and "25%" in txt


def test_year_summary_no_payout():
    phases = [{"phase": "a", "payout": 0}, {"phase": "b", "payout": 0}]
    txt = explain_year(2024, phases, sum_insured=10000)
    assert "no payout" in txt


def test_burning_cost_explanation():
    txt = explain_burning_cost(1300, 10000, 5)
    assert "1,300" in txt
    assert "13.0%" in txt
    assert "burning cost" in txt
