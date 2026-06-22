"""Simulation Engine tests (Section 4.6) - projections, CPI, FX, horizons."""
import pytest

from app.engines.simulation_engine import SimulationEngine

A = pytest.approx


def run(**kw):
    base = dict(initial_value=1_000_000, expected_return_pct=8, volatility_pct=15, horizon="year")
    base.update(kw)
    return SimulationEngine(seed=11).run(**base)


def test_deterministic_with_seed():
    a = run()
    b = run()
    assert a.nominal.p50 == b.nominal.p50
    assert a.probability_of_loss_real == b.probability_of_loss_real


def test_horizon_mapping():
    assert run(horizon="year").horizon_years == A(1.0)
    assert run(horizon="quarter").horizon_years == A(0.25)
    assert run(horizon="month").horizon_years == A(1 / 12)


def test_longer_horizon_widens_distribution():
    month = run(horizon="month")
    year = run(horizon="year")
    assert (year.nominal.p95 - year.nominal.p5) > (month.nominal.p95 - month.nominal.p5)


def test_inflation_pulls_real_below_nominal():
    r = run(cpi_pct=5, fx_change_pct=0)
    assert r.real.p50 < r.nominal.p50


def test_positive_fx_drift_raises_value():
    base = run(fx_change_pct=0)
    up = run(fx_change_pct=10)
    assert up.nominal.mean > base.nominal.mean


def test_zero_volatility_is_deterministic():
    r = run(volatility_pct=0, cpi_pct=0, fx_change_pct=0)
    assert r.nominal.p5 == A(r.nominal.p95, rel=1e-6)


def test_probabilities_complementary():
    r = run()
    assert r.probability_of_loss_real + r.probability_of_gain_real == A(1.0)


def test_invalid_horizon_raises():
    with pytest.raises(ValueError):
        run(horizon="decade")
