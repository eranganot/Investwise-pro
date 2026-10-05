"""PR 88 - the efficient-frontier engine, checked against closed forms.

Every assertion here is about a number that can be derived by hand. An
optimiser tested only against itself proves it is deterministic, not that it
is right.
"""
import math

import numpy as np
import pytest

from app.engines import frontier as fr
from app.engines.return_convention import drift_pct


def _cov2(s1, s2, rho):
    c = rho * s1 * s2
    return np.array([[s1 * s1, c], [c, s2 * s2]])


# --------------------------------------------------------------------------
# the optimiser against two-asset closed forms
# --------------------------------------------------------------------------

def test_two_asset_min_variance_matches_the_closed_form():
    s1, s2, rho = 0.20, 0.06, 0.1
    cov = _cov2(s1, s2, rho)
    c = rho * s1 * s2
    expected = (s2 * s2 - c) / (s1 * s1 + s2 * s2 - 2 * c)
    w = fr.optimize([0.08, 0.03], cov, risk_aversion=math.inf, free_total=1.0)
    assert w[0] == pytest.approx(expected, abs=1e-6)
    assert w.sum() == pytest.approx(1.0)


def test_two_asset_mean_variance_matches_the_closed_form():
    # max m'w - lam/2 w'Sw, w2 = 1 - w1: first-order condition gives
    # w1 = (m1 - m2 + lam (s2^2 - c)) / (lam (s1^2 + s2^2 - 2c)), interior here.
    m1, m2, s1, s2, rho, lam = 0.09, 0.035, 0.18, 0.06, 0.2, 4.0
    cov = _cov2(s1, s2, rho)
    c = rho * s1 * s2
    expected = (m1 - m2 + lam * (s2 * s2 - c)) / (lam * (s1 * s1 + s2 * s2 - 2 * c))
    assert 0 < expected < 1
    w = fr.optimize([m1, m2], cov, risk_aversion=lam, free_total=1.0)
    assert w[0] == pytest.approx(expected, abs=1e-6)


def test_a_constant_added_to_every_return_does_not_move_the_weights():
    """The docstring's claim: total or excess returns give the same answer."""
    cov = _cov2(0.18, 0.06, 0.2)
    a = fr.optimize([0.09, 0.035], cov, risk_aversion=3.0, free_total=1.0)
    b = fr.optimize([0.09 - 0.04, 0.035 - 0.04], cov, risk_aversion=3.0, free_total=1.0)
    assert np.allclose(a, b, atol=1e-7)


def test_the_corner_is_long_only():
    # Low risk aversion and a dominant asset: unconstrained would short asset 2.
    w = fr.optimize([0.20, 0.02], _cov2(0.15, 0.05, 0.0), risk_aversion=0.5, free_total=1.0)
    assert w[0] == pytest.approx(1.0, abs=1e-9) and w[1] == pytest.approx(0.0, abs=1e-9)


def test_caps_hold_and_infeasible_caps_refuse():
    cov = np.diag([0.04, 0.01, 0.0025])
    w = fr.optimize([0.10, 0.05, 0.03], cov, risk_aversion=1.0, free_total=1.0,
                    upper=[0.4, 1.0, 1.0])
    assert w[0] <= 0.4 + 1e-9 and w.sum() == pytest.approx(1.0)
    with pytest.raises(ValueError, match="infeasible"):
        fr.optimize([0.1, 0.05], np.diag([0.04, 0.01]), risk_aversion=1.0,
                    free_total=1.0, upper=[0.3, 0.3])


# --------------------------------------------------------------------------
# THE POINT OF THE MODULE: the core is chosen for the book it lives in
# --------------------------------------------------------------------------

def test_a_heavy_equity_sleeve_pushes_the_core_toward_what_offsets_it():
    # assets: leveraged equity sleeve, equity, bonds, gold
    vols = np.array([0.60, 0.16, 0.06, 0.15])
    corr = np.array([[1.0, 0.9, -0.2, 0.05],
                     [0.9, 1.0, -0.2, 0.05],
                     [-0.2, -0.2, 1.0, 0.3],
                     [0.05, 0.05, 0.3, 1.0]])
    cov = corr * np.outer(vols, vols)
    mu = np.array([0.20, 0.08, 0.04, 0.05])
    free = np.array([False, True, True, True])

    alone = fr.optimize(mu, cov, risk_aversion=3.0, free_total=1.0, free=free)
    beside = fr.optimize(mu, cov, risk_aversion=3.0, free_total=0.36, free=free,
                         fixed=np.array([0.64, 0, 0, 0]))

    assert beside[0] == pytest.approx(0.64)              # the sleeve did not move
    assert beside[1:].sum() == pytest.approx(0.36)
    eq_share_alone = alone[1] / alone[1:].sum()
    eq_share_beside = beside[1] / beside[1:].sum()
    assert eq_share_beside < eq_share_alone - 0.05       # it leans away from equity


def test_a_free_asset_with_a_fixed_weight_is_refused():
    with pytest.raises(ValueError, match="free asset"):
        fr.optimize([0.1, 0.05], np.diag([0.04, 0.01]), risk_aversion=1.0,
                    free_total=1.0, fixed=np.array([0.2, 0.0]))


# --------------------------------------------------------------------------
# covariance
# --------------------------------------------------------------------------

def test_ledoit_wolf_shrinks_more_when_the_sample_says_less():
    rng = np.random.default_rng(3)
    true = _cov2(0.01, 0.012, 0.5)
    long = rng.multivariate_normal([0, 0], true, size=5000)
    short = rng.multivariate_normal(np.zeros(8), np.eye(8) * 1e-4, size=12)

    cov_long, k_long = fr.ledoit_wolf(long, periods=1)
    _, k_short = fr.ledoit_wolf(short, periods=1)
    assert 0.0 <= k_long < k_short <= 1.0
    assert np.allclose(cov_long, true, rtol=0.1)


def test_ledoit_wolf_is_invertible_when_the_sample_is_not():
    rng = np.random.default_rng(5)
    x = rng.normal(0, 0.01, size=(10, 20))               # N > T: sample is singular
    cov, k = fr.ledoit_wolf(x)
    assert np.linalg.eigvalsh(cov).min() > 0 and k > 0


def test_ledoit_wolf_annualises():
    rng = np.random.default_rng(1)
    x = rng.normal(0, 0.01, size=(2000, 2))
    daily, _ = fr.ledoit_wolf(x, periods=1)
    annual, _ = fr.ledoit_wolf(x)
    assert np.allclose(annual, daily * 252)


# --------------------------------------------------------------------------
# the projection
# --------------------------------------------------------------------------

def test_projection_lands_in_the_set_and_is_idempotent():
    rng = np.random.default_rng(9)
    up = np.array([0.5, 0.3, 1.0, 0.2])
    for _ in range(50):
        v = rng.normal(0, 1, 4)
        p = fr.project_capped_simplex(v, up, 0.8)
        assert p.sum() == pytest.approx(0.8, abs=1e-9)
        assert np.all(p >= -1e-12) and np.all(p <= up + 1e-12)
        assert np.allclose(fr.project_capped_simplex(p, up, 0.8), p, atol=1e-9)


# --------------------------------------------------------------------------
# the frontier
# --------------------------------------------------------------------------

def _three():
    vols = np.array([0.16, 0.06, 0.15])
    corr = np.array([[1, -0.2, 0.05], [-0.2, 1, 0.3], [0.05, 0.3, 1]])
    return np.array([0.08, 0.035, 0.05]), corr * np.outer(vols, vols)


def test_the_frontier_rises_in_both_return_and_risk_and_dominates_its_inputs():
    mu, cov = _three()
    pts = fr.frontier(mu, cov, free_total=1.0)
    rets = [p["return"] for p in pts]
    vols = [p["volatility"] for p in pts]
    assert rets == sorted(rets) and vols == sorted(vols)
    assert math.isinf(pts[0]["risk_aversion"])
    # No single asset beats the frontier: at its own volatility the frontier
    # returns at least as much.
    for i in range(3):
        w = np.eye(3)[i]
        st = fr.portfolio_stats(w, mu, cov)
        best = max((p["return"] for p in pts if p["volatility"] <= st["volatility"] + 1e-9),
                   default=None)
        assert best is not None and best >= st["return"] - 1e-6


def test_for_volatility_hits_the_target_and_says_when_it_cannot():
    mu, cov = _three()
    hit = fr.for_volatility(mu, cov, 0.08, free_total=1.0)
    assert hit["reachable"] and hit["volatility"] == pytest.approx(0.08, abs=1e-5)

    low = fr.for_volatility(mu, cov, 0.001, free_total=1.0)
    assert not low["reachable"] and low["bound"] == "MIN_VARIANCE"
    high = fr.for_volatility(mu, cov, 0.90, free_total=1.0)
    assert not high["reachable"] and high["bound"] == "MAX_RETURN"


# --------------------------------------------------------------------------
# Kelly
# --------------------------------------------------------------------------

def test_single_asset_kelly_is_excess_over_variance():
    mu, sig, r = 0.10, 0.22, 0.04
    w = fr.kelly_weights([mu], [[sig * sig]], r)
    assert w[0] == pytest.approx((mu - r) / sig ** 2)


def test_growth_peaks_at_full_kelly_and_three_x_is_over_kelly():
    """The plan's illustration, as arithmetic: 3x a 10%/22% asset grows ~0.2%."""
    mu, sig, r = 0.10, 0.22, 0.04
    cov = [[sig * sig]]
    k = (mu - r) / sig ** 2
    g_kelly = fr.growth([k], [mu], cov, r)["log_growth"]
    for f in (0.5 * k, 0.9 * k, 1.1 * k, 2 * k):
        assert fr.growth([f], [mu], cov, r)["log_growth"] < g_kelly
    assert g_kelly == pytest.approx(0.0772, abs=1e-3)
    assert fr.growth([3.0], [mu], cov, r)["log_growth"] == pytest.approx(0.0022, abs=1e-3)

    pos = fr.kelly_position([3.0], [mu], cov, r)
    assert pos["over_kelly"] and pos["fraction_of_kelly"] == pytest.approx(3.0 / k)
    assert pos["growth_at_kelly"]["log_growth"] == pytest.approx(g_kelly)
    assert not fr.kelly_position([0.5], [mu], cov, r)["over_kelly"]


def test_a_mix_with_no_excess_return_has_no_kelly_size():
    pos = fr.kelly_position([1.0], [0.03], [[0.04]], 0.04)
    assert pos["fraction_of_kelly"] is None and pos["over_kelly"]
    assert pos["reason"] == "NO_EXCESS_RETURN"


def test_unconstrained_optimum_is_the_kelly_fraction_of_kelly():
    """lambda = 1/fraction: the dial the objectives are stated in."""
    mu, cov = _three()
    r = 0.03
    lam = fr.risk_aversion_for("Grow")
    assert lam == pytest.approx(2.0)
    half = np.linalg.solve(cov, mu - r) / lam
    assert np.allclose(half, 0.5 * fr.kelly_weights(mu, cov, r))
    assert fr.risk_aversion_for(None) == pytest.approx(3.0)
    assert fr.risk_aversion_for("Unknown") == pytest.approx(3.0)
    assert all(f < 1.0 for f in fr.KELLY_FRACTION_BY_OBJECTIVE.values())


def test_arithmetic_drift_goes_through_the_one_convention():
    assert fr.arithmetic_drift(10.0, 20.0) == pytest.approx(drift_pct(10.0, 20.0) / 100)
    g = fr.growth([1.0], [fr.arithmetic_drift(10.0, 20.0)], [[0.04]], 0.0)
    assert g["cagr"] == pytest.approx(0.10, abs=1e-9)    # round-trips to the CAGR
