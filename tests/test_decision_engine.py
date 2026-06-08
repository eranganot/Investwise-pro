"""Decision Engine tests (Section 4.5) - Impact Score formula + gates."""
import pytest

from app.engines.decision_engine import DecisionEngine
from app.schemas.state_machine import (
    ActionType, DetectedSignal, Market, OptimizedSignal, VettedSignal,
)

A = pytest.approx


def optimized(*, expected_return=10.0, divergence=0.0, gross=None, net=None,
              prob_ruin=None, depth=3) -> OptimizedSignal:
    det = DetectedSignal(
        ticker="X", market=Market.NYSE, action_type=ActionType.BUY, trigger="t",
        depth=depth, divergence_pct=divergence, expected_return_pct=expected_return,
        gross_gain_ils=gross,
    )
    vet = VettedSignal(source=det, probability_of_ruin=prob_ruin)
    return OptimizedSignal(source=vet, net_gain_delta=net)


def test_impact_formula_matches_spec():
    # R: max(10, 0)*5 = 50 ; T: 75000/100000 = 75 ; Risk: (1-0.10)*100 = 90
    r = DecisionEngine().rank(optimized(expected_return=10, gross=100_000, net=75_000, prob_ruin=0.10))
    assert r.r_score == A(50)
    assert r.t_score == A(75)
    assert r.risk_score == A(90)
    # (0.4*50 + 0.3*75 + 0.3*90) / 2 = 34.75
    assert r.impact_score == A(34.75)


def test_tax_neutral_when_no_economics():
    r = DecisionEngine().rank(optimized(gross=None, net=None))
    assert r.t_score == A(50)


def test_risk_neutral_when_not_assessed():
    r = DecisionEngine().rank(optimized(prob_ruin=None))
    assert r.risk_score == A(50)


def test_divergence_drives_return_when_no_expected_return():
    r = DecisionEngine().rank(optimized(expected_return=0.0, divergence=8.0))
    assert r.r_score == A(40)  # |8| * 5


def test_confidence_increases_with_depth_and_data():
    shallow = DecisionEngine().rank(optimized(depth=1, prob_ruin=None, net=None))
    deep = DecisionEngine().rank(optimized(depth=3, prob_ruin=0.05, gross=100_000, net=80_000))
    assert deep.confidence > shallow.confidence
    assert 0 <= shallow.confidence <= 100 and 0 <= deep.confidence <= 100
