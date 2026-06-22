"""Decision Engine tests (Section Z) - 5-component Impact + confidence."""
import pytest

from app.engines.decision_engine import DecisionEngine
from app.schemas.state_machine import (
    ActionType, DetectedSignal, Market, OptimizedSignal, VettedSignal,
)

A = pytest.approx


def optimized(*, expected_return=10.0, divergence=0.0, gross=None, net=None,
              prob_ruin=None, depth=3, action=ActionType.BUY, liquidity=None) -> OptimizedSignal:
    det = DetectedSignal(
        ticker="X", market=Market.NYSE, action_type=action, trigger="t",
        depth=depth, divergence_pct=divergence, expected_return_pct=expected_return,
        gross_gain_ils=gross, liquidity_score=liquidity,
    )
    vet = VettedSignal(source=det, probability_of_ruin=prob_ruin)
    return OptimizedSignal(source=vet, net_gain_delta=net)


def test_weak_incomplete_signal_scores_low():
    # depth-1, tiny divergence, no economics -> should fall below the impact gate
    r = DecisionEngine().rank(optimized(expected_return=0.0, divergence=1.0, depth=1))
    assert r.impact_score < 20


def test_impact_formula_matches_spec():
    r = DecisionEngine().rank(optimized(expected_return=10, gross=100_000, net=75_000,
                                        prob_ruin=0.10, depth=3, action=ActionType.BUY))
    assert r.scores.ret == A(50)        # max(10,0)*5
    assert r.scores.tax == A(75)        # 75000/100000*100
    assert r.scores.risk == A(90)       # (1-0.10)*100
    assert r.scores.conviction == A(100)  # depth 3
    # liquidity is unknown -> NEUTRAL (weighted avg of known dims), not a fake 25
    assert r.scores.liquidity == A(73.6111, abs=0.01)
    assert r.complexity_label == "Easy" and r.complexity_factor == A(1.25)
    # impact = (weighted over known, renormalized) / 1.25
    assert r.impact_score == A(58.8889, abs=0.01)


def test_tax_unknown_is_neutral_not_penalized():
    # known dims = return(50) + conviction(100); neutral = (.30*50+.10*100)/.40 = 62.5
    assert DecisionEngine().rank(optimized(gross=None, net=None)).scores.tax == A(62.5)


def test_risk_unknown_is_neutral_not_penalized():
    assert DecisionEngine().rank(optimized(prob_ruin=None)).scores.risk == A(62.5)


def test_divergence_drives_return_when_no_expected_return():
    assert DecisionEngine().rank(optimized(expected_return=0.0, divergence=8.0)).scores.ret == A(40)


def test_complexity_factor_divides_impact():
    buy = DecisionEngine().rank(optimized(action=ActionType.BUY))      # Easy 1.25
    tax = DecisionEngine().rank(optimized(action=ActionType.TAX))      # Difficult 1.75
    assert buy.complexity_factor == A(1.25) and tax.complexity_factor == A(1.75)
    assert buy.impact_score > tax.impact_score  # same inputs, higher complexity -> lower impact


def test_confidence_breakdown_is_complete_and_weighted():
    r = DecisionEngine().rank(optimized(expected_return=10, gross=100_000, net=80_000,
                                        prob_ruin=0.05, depth=3), historical_accuracy=75)
    b = r.confidence_breakdown
    assert 0 <= b.data_quality <= 100 and 0 <= b.market_stability <= 100
    assert b.historical_accuracy == A(75)
    # confidence equals the weighted sum of its breakdown
    expected = 0.40 * b.data_quality + 0.30 * b.model_agreement + 0.20 * b.historical_accuracy + 0.10 * b.market_stability
    assert r.confidence == A(expected)


def test_return_scale_is_config_driven():
    from app.core.config import Settings
    base = DecisionEngine().rank(optimized(expected_return=10, divergence=0)).scores.ret
    doubled = DecisionEngine(Settings(decision_return_scale=10.0)).rank(
        optimized(expected_return=10, divergence=0)).scores.ret
    assert base == A(50) and doubled == A(100)
