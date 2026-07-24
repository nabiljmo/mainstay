import numpy as np
import pytest

from app.pricing import apply_loadings, expected_loss


# ---------------- burning cost & expected loss ----------------

def test_burning_cost_is_mean_of_payouts():
    r = expected_loss([2000, 0, 4000, 0, 1000], sum_insured=10000)
    assert r["burning_cost"] == 1400.0  # (2000+0+4000+0+1000)/5


def test_technical_el_takes_the_max():
    r = expected_loss([2000, 0, 4000, 0, 1000], sum_insured=10000, dist="gamma")
    assert r["technical_el"] == max(r["burning_cost"], r["modelled_el"])
    assert r["technical_el"] >= r["burning_cost"]


def test_frequency_is_share_of_paying_years():
    r = expected_loss([0, 0, 500, 1000, 0], sum_insured=10000)
    assert r["frequency"] == 0.4  # 2 of 5 years paid


def test_all_zero_history_prices_to_zero():
    r = expected_loss([0, 0, 0, 0], sum_insured=10000)
    assert r["burning_cost"] == 0.0
    assert r["technical_el"] == 0.0


def test_too_few_positive_years_falls_back_to_burning_cost():
    r = expected_loss([0, 0, 0, 3000], sum_insured=10000, dist="gamma")
    # only one positive point -> cannot fit -> modelled == burning cost
    assert r["fit_params"] is None
    assert r["modelled_el"] == r["burning_cost"]


def test_qq_has_one_point_per_positive_year():
    r = expected_loss([1000, 2000, 0, 3000, 500], sum_insured=10000, dist="gamma")
    assert len(r["qq"]) == 4  # four positive years


# ---------------- distribution fit sanity ----------------

def test_gamma_fit_recovers_known_parameters():
    rng = np.random.default_rng(0)
    true_shape, true_scale = 2.0, 1500.0
    sample = rng.gamma(true_shape, true_scale, size=4000).tolist()
    r = expected_loss(sample, sum_insured=10_000_000, dist="gamma", n_sim=1000)
    assert r["fit_params"]["shape"] == pytest.approx(true_shape, rel=0.15)
    assert r["fit_params"]["scale"] == pytest.approx(true_scale, rel=0.15)
    # modelled EL near the true mean (shape*scale = 3000), all years positive.
    assert r["modelled_el"] == pytest.approx(true_shape * true_scale, rel=0.1)


# ---------------- loadings & gross-up (golden) ----------------

def test_loading_gross_up_hand_calculated():
    # EL 1000; +20% of EL; +50 flat; +25% of gross.
    # base = 1000 + 200 + 50 = 1250 ; gross = 1250 / (1 - 0.25) = 1666.67
    loadings = [
        {"name": "unc", "basis": "pct_el", "value": 20},
        {"name": "fee", "basis": "flat", "value": 50},
        {"name": "profit", "basis": "pct_gross", "value": 25},
    ]
    r = apply_loadings(1000, loadings, sum_insured=10000)
    assert r["gross_premium"] == pytest.approx(1666.67, abs=0.01)
    assert r["premium_rate"] == pytest.approx(16.67, abs=0.01)


def test_pct_gross_amounts_sum_correctly():
    loadings = [
        {"name": "a", "basis": "pct_gross", "value": 10},
        {"name": "b", "basis": "pct_gross", "value": 15},
    ]
    r = apply_loadings(1000, loadings, sum_insured=10000)
    # base = 1000 ; g = 0.25 ; gross = 1333.33
    assert r["gross_premium"] == pytest.approx(1333.33, abs=0.01)
    gross_amts = sum(b["amount"] for b in r["loading_breakdown"] if b["basis"] == "pct_gross")
    assert gross_amts == pytest.approx(333.33, abs=0.02)


def test_impossible_gross_loadings_rejected():
    loadings = [{"name": "x", "basis": "pct_gross", "value": 100}]
    with pytest.raises(ValueError):
        apply_loadings(1000, loadings, sum_insured=10000)


def test_no_loadings_gross_equals_el():
    r = apply_loadings(1500, [], sum_insured=10000)
    assert r["gross_premium"] == 1500.0
    assert r["premium_rate"] == 15.0
