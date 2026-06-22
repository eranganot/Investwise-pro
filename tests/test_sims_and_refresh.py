"""Monte Carlo goal-probability output + hourly market-refresh state."""
from app.engines.simulation_engine import SimulationEngine
from app.services import market_state


def test_probability_meets_target_is_computed():
    eng = SimulationEngine(seed=7)
    # an easy target (below today's value) -> almost certain
    easy = eng.run(initial_value=100_000, expected_return_pct=6, volatility_pct=12,
                   horizon_years=10, target_value=50_000)
    # a very hard target -> almost impossible
    hard = eng.run(initial_value=100_000, expected_return_pct=6, volatility_pct=12,
                   horizon_years=10, target_value=10_000_000)
    assert easy.probability_meets_target is not None
    assert easy.probability_meets_target > 0.95
    assert hard.probability_meets_target < 0.05
    # and without a target it stays None
    none = eng.run(initial_value=100_000, expected_return_pct=6, volatility_pct=12, horizon_years=10)
    assert none.probability_meets_target is None


def test_market_refresh_state():
    before = market_state.status()
    assert before["refresh_interval_minutes"] == 60
    assert before["last_refreshed"]                      # seeded at import
    out = market_state.refresh_market_data()
    assert out["refresh_count"] >= 1
    assert out["last_refreshed"] >= before["last_refreshed"]
