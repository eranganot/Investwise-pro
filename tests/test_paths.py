"""PR 93 - filtered historical paths, checked against closed forms."""
import numpy as np
import pytest

from app.engines import paths
from app.services import frontier_service as fs


def test_recentre_moves_the_mean_and_keeps_the_shape():
    rng = np.random.default_rng(0)
    r = rng.normal(0.002, 0.01, size=(1000, 3))
    out = paths.recentre(r, np.array([0.05, 0.10, -0.02]))
    assert np.allclose(out.mean(axis=0), np.array([0.05, 0.10, -0.02]) / 252)
    assert np.allclose(out.std(axis=0), r.std(axis=0))


def test_constant_returns_compound_exactly_and_never_draw_down():
    daily = np.full(500, 0.001)
    sim = paths.simulate(daily, years=1.0, n_paths=50)
    steps = sim["steps"]
    assert np.allclose(sim["growth"], 1.001 ** steps)
    months = np.arange(21, steps + 1, 21)
    s = sum(1.001 ** (steps - m) for m in months)
    assert np.allclose(sim["deposit_factor"], s)
    assert np.allclose(sim["worst_drawdown"], 0.0)


def test_the_deposit_lever_is_exact_on_the_paths():
    daily = np.full(500, 0.0005)
    sim = paths.simulate(daily, years=2.0, n_paths=20)
    g, s = sim["growth"][0], sim["deposit_factor"][0]
    st = paths.goal_stats(sim, nav=1000, target=5000, monthly=0)
    assert st["extra_monthly_needed"] == pytest.approx((5000 - 1000 * g) / s)
    assert st["p_goal_pct"] == 0.0
    done = paths.goal_stats(sim, nav=1000, target=5000, monthly=st["extra_monthly_needed"] + 1e-6)
    assert done["p_goal_pct"] == 100.0


def test_crashes_that_cluster_make_halving_likelier_than_their_volatility_suggests():
    rng = np.random.default_rng(3)
    calm = rng.normal(0.0004, 0.02, 2500)
    crashy = rng.normal(0.0012, 0.008, 2500)
    for start in range(200, 2500, 400):                 # runs of bad days
        crashy[start:start + 15] = -0.035
    crashy *= calm.std() / crashy.std()
    crashy += calm.mean() - crashy.mean()
    a = paths.goal_stats(paths.simulate(calm, years=5), nav=1, target=2)
    b = paths.goal_stats(paths.simulate(crashy, years=5), nav=1, target=2)
    assert b["p_halve_pct"] > a["p_halve_pct"]


def test_the_same_seed_gives_the_same_paths():
    daily = np.random.default_rng(1).normal(0.0005, 0.02, 800)
    a = paths.simulate(daily, years=3)
    b = paths.simulate(daily, years=3)
    assert np.array_equal(a["growth"], b["growth"])


def test_expand_inverts_reduce():
    rng = np.random.default_rng(2)
    n = 7
    a = rng.normal(size=(n, n))
    cov = a @ a.T
    mu = rng.normal(size=n)
    sleeve_idx, cash_idx = [4, 5], 6
    core = np.zeros(n)
    core[0], core[2] = 0.6, 0.4
    w_r = np.array([0.2, 0.1, 0.65, 0.05])
    mu_r, cov_r = fs._reduce(mu, cov, sleeve_idx, core, cash_idx)
    w = fs._expand(w_r, sleeve_idx, core, cash_idx, n)
    assert w.sum() == pytest.approx(1.0)
    assert w @ mu == pytest.approx(w_r @ mu_r)
    assert w @ cov @ w == pytest.approx(w_r @ cov_r @ w_r)
