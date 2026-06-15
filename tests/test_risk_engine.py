"""Risk Engine tests (Section 4.4) - Monte Carlo, ruin, caps, veto.

Uses a fixed seed for deterministic Monte Carlo. Defaults: volatility cap 15%,
max drawdown cap 20%, ruin-probability cap 20%.
"""
import pytest

from app.engines.risk_engine import RiskEngine
from app.engines.state_machine import StateMachine
from app.schemas.state_machine import (
    ActionType,
    DetectedSignal,
    Market,
    VetoedSignal,
)


def signal(vol=None, mu=8.0, action=ActionType.BUY, divergence=8.0) -> DetectedSignal:
    return DetectedSignal(
        ticker="X", market=Market.NYSE, action_type=action,
        trigger="t", depth=2, divergence_pct=divergence,
        expected_return_pct=mu, volatility_pct=vol,
    )


def test_monte_carlo_is_deterministic_with_seed():
    a = RiskEngine(seed=123).monte_carlo(0.08, 0.18)
    b = RiskEngine(seed=123).monte_carlo(0.08, 0.18)
    assert a.probability_of_ruin == b.probability_of_ruin
    assert a.median_max_drawdown == b.median_max_drawdown


def test_probability_of_ruin_is_a_valid_probability():
    a = RiskEngine(seed=1).monte_carlo(0.08, 0.20)
    assert 0.0 <= a.probability_of_ruin <= 1.0
    assert a.runs == RiskEngine().settings.monte_carlo_runs


def test_higher_volatility_increases_ruin_probability():
    low = RiskEngine(seed=5).monte_carlo(0.08, 0.10)
    high = RiskEngine(seed=5).monte_carlo(0.08, 0.30)
    assert high.probability_of_ruin > low.probability_of_ruin


def test_low_volatility_not_vetoed():
    v = RiskEngine(seed=42).vet(signal(vol=10.0))
    assert v.veto_flag is False
    assert v.volatility == pytest.approx(0.10)
    assert v.probability_of_ruin is not None


def test_high_volatility_is_vetoed_by_cap():
    v = RiskEngine(seed=42).vet(signal(vol=40.0))
    assert v.veto_flag is True
    assert "volatility" in v.risk_critique.lower()


def test_volatility_exactly_at_cap_not_vetoed_by_vol_rule():
    # 15% == cap; cap is a strict ">" so it should not trip the volatility rule.
    v = RiskEngine(seed=42).vet(signal(vol=15.0))
    assert "volatility 15%" not in v.risk_critique.lower() or v.veto_flag is False


def test_awaiting_data_when_no_volatility():
    v = RiskEngine(seed=42).vet(signal(vol=None))
    assert v.veto_flag is False
    assert "Awaiting Data" in v.risk_critique


def test_high_risk_blocks_the_pipeline():
    sm = StateMachine(risk=RiskEngine(seed=42))
    result = sm.run(signal(vol=45.0, divergence=12.0))  # high ROI, but vetoed
    assert isinstance(result, VetoedSignal)


def test_ruin_probability_cap_is_config_driven():
    from app.core.config import Settings
    # Force an absurdly low ruin tolerance -> even modest vol should veto.
    eng = RiskEngine(Settings(ruin_probability_cap=0.0), seed=42)
    _, veto, _ = eng.assess(expected_return_pct=8.0, volatility_pct=10.0)
    assert veto is True
