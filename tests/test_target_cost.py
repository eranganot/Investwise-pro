"""T3 - what the target costs. Drawdown asymmetry, the distribution, the tax.

The load-bearing test here is the geometric-to-arithmetic conversion. A measured
CAGR is what a path actually compounded at, so it belongs on the MEDIAN of a
lognormal projection. SimulationEngine draws exp((mu - sigma^2/2)T + ...), so
feeding a CAGR in as `mu` shifts every percentile down by the volatility drag --
silently, and by more the more volatile the blend. Which is exactly backwards
for a card whose job is to show the median honestly.
"""
from __future__ import annotations

import math

import pytest

from app.services import target_solver as ts


# ------------------------------------------------------------ drawdown
def test_recovery_is_asymmetric_and_that_is_the_point():
    assert ts.recovery_pct(50.0) == pytest.approx(100.0, abs=0.01)
    assert ts.recovery_pct(45.0) == pytest.approx(81.82, abs=0.01)
    assert ts.recovery_pct(80.0) == pytest.approx(400.0, abs=0.01)
    assert ts.recovery_pct(0.0) == 0.0


def test_recovery_always_exceeds_the_fall_for_any_real_drawdown():
    for dd in (1.0, 10.0, 33.3, 60.0, 90.0):
        assert ts.recovery_pct(dd) > dd


# ------------------------------------------- geometric vs arithmetic drift
def test_a_measured_cagr_anchors_the_median_not_the_mean():
    """mu must be raised by sigma^2/2 so the median lands on the measured CAGR."""
    cagr, vol = 12.0, 30.0
    mu = ts.geometric_to_arithmetic_pct(cagr, vol)
    sigma = vol / 100.0
    # The engine's median factor over one year is exp(mu - sigma^2/2).
    median_growth = math.exp(mu / 100.0 - 0.5 * sigma ** 2) - 1.0
    assert median_growth == pytest.approx(cagr / 100.0, abs=1e-9)


def test_zero_volatility_leaves_the_drift_alone_apart_from_log_conversion():
    mu = ts.geometric_to_arithmetic_pct(10.0, 0.0)
    assert mu == pytest.approx(math.log1p(0.10) * 100.0, abs=1e-6)


def test_the_conversion_grows_with_volatility_because_the_drag_does():
    low = ts.geometric_to_arithmetic_pct(10.0, 10.0)
    high = ts.geometric_to_arithmetic_pct(10.0, 60.0)
    assert high > low


# ------------------------------------------------------------- cost_of
def _measured(**kw) -> dict:
    base = {"cagr_pct": 12.0, "volatility_pct": 28.0, "max_drawdown_pct": 45.0,
            "gross_cagr_pct": 14.5, "tax_drag_pct": 2.5, "cgt_rate_pct": 25.0}
    base.update(kw)
    return base


def test_drawdown_is_reported_in_shekels_at_the_book_size():
    c = ts.cost_of(_measured(), nav_ils=20_000.0, horizon_years=10.0)
    assert c["drawdown"]["pct"] == 45.0
    assert c["drawdown"]["ils"] == pytest.approx(9_000.0)
    assert c["drawdown"]["recovery_pct"] == pytest.approx(81.82, abs=0.01)


def test_the_median_sits_below_the_mean_on_a_volatile_blend():
    """The gap IS the finding: a target expressed as an average is not the
    outcome you are most likely to get."""
    c = ts.cost_of(_measured(volatility_pct=45.0), nav_ils=20_000.0,
                   horizon_years=10.0)
    p = c["projection"]
    assert p["real"]["median_ils"] < p["real"]["mean_ils"]
    assert p["median_below_mean_pct"] > 0


def test_a_calmer_blend_has_a_smaller_median_to_mean_gap():
    calm = ts.cost_of(_measured(volatility_pct=10.0), nav_ils=20_000.0)
    wild = ts.cost_of(_measured(volatility_pct=50.0), nav_ils=20_000.0)
    assert wild["projection"]["median_below_mean_pct"] > \
        calm["projection"]["median_below_mean_pct"]


def test_the_projection_leads_in_real_terms_and_says_so():
    c = ts.cost_of(_measured(), nav_ils=20_000.0)
    p = c["projection"]
    assert p["basis"] == "real"
    assert p["real"]["median_ils"] < p["nominal"]["median_ils"]   # CPI-deflated
    assert any("purchasing power" in a for a in p["assumptions"])
    assert any("MEDIAN" in a for a in p["assumptions"])


def test_the_projection_is_seeded_so_it_does_not_move_between_refreshes():
    a = ts.cost_of(_measured(), nav_ils=20_000.0)
    b = ts.cost_of(_measured(), nav_ils=20_000.0)
    assert a["projection"]["real"]["median_ils"] == b["projection"]["real"]["median_ils"]


def test_tax_reports_the_cost_to_stay_and_names_it_as_such():
    c = ts.cost_of(_measured(), nav_ils=20_000.0)
    t = c["tax"]
    assert t["gross_cagr_pct"] == 14.5 and t["net_cagr_pct"] == 12.0
    assert t["drag_pct_per_year"] == 2.5
    assert "arrive" in t["note"]          # the funding CGT is the other half


def test_no_nav_means_no_projection_rather_than_a_projection_of_nothing():
    c = ts.cost_of(_measured(), nav_ils=0.0)
    assert c["projection"] is None
    assert c["drawdown"]["ils"] == 0.0
