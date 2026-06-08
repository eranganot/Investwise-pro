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


def test_impact_formula_matches_spec():
    r = DecisionEngine().rank(optimized(expected_return=10, gross=100_000, net=75_000,
                                        prob_ruin=0.10, depth=3, action=ActionType.BUY))
    assert r.scores.ret == A(50)        # max(10,0)*5
    assert r.scores.tax == A(75)        # 75000/100000*100
    assert r.scores.risk == A(90)       # (1-0.10)*100
    assert r.scores.liquidity == A(50)  # neutral
    assert r.scores.conviction == A(100)  # depth 3
    assert r.complexity_label == "Easy" and r.complexity_factor == A(1.25)
    # (0.30*50 + 0.25*75 + 0.25*90 + 0.10*50 + 0.10*100) / 1.25 = 57.0
    assert r.impact_score == A(57.0)


def test_tax_neutral_when_no_economics():
    assert DecisionEngine().rank(optimized(gross=None, net=None)).scores.tax == A(50)


def test_risk_neutral_when_not_assessed():
    assert DecisionEngine().rank(optimized(prob_ruin=None)).scores.risk == A(50)


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
