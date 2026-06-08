"""Explainability engine tests (Section AF)."""
from app.engines.risk_engine import RiskEngine
from app.engines.state_machine import StateMachine
from app.engines.xai_engine import XaiEngine, recommendation_id
from app.schemas.state_machine import ActionType, DetectedSignal, DisplayedItem, Market


def _displayed(action=ActionType.BUY, vol=12):
    sm = StateMachine(risk=RiskEngine(seed=7))
    r = sm.run(DetectedSignal(ticker="TEVA", market=Market.NYSE, action_type=action,
               trigger="Depth 3 backbone divergence +8.2%", depth=3, divergence_pct=8.2,
               expected_return_pct=10, volatility_pct=vol))
    assert isinstance(r, DisplayedItem)
    return r


def test_explanation_has_full_contract():
    exp = XaiEngine().build(_displayed())
    d = exp.model_dump()
    for key in ("recommendation_id", "why_now", "supporting_factors", "contradicting_factors",
                "assumptions", "confidence_breakdown", "expected_outcomes", "failure_conditions"):
        assert key in d
    assert d["supporting_factors"] and d["contradicting_factors"] and d["failure_conditions"]
    assert set(d["confidence_breakdown"]["components"]) == {
        "data_quality", "model_agreement", "historical_accuracy", "market_stability"}
    assert d["expected_outcomes"]["risk_profile_variance"] in {"INCREASE", "DECREASE", "NEUTRAL"}


def test_recommendation_id_is_stable():
    assert recommendation_id("TEVA", "Buy") == recommendation_id("TEVA", "Buy")
    assert recommendation_id("TEVA", "Buy") != recommendation_id("GOLD", "Buy")


def test_bulletproof_path_decreases_risk_profile():
    exp = XaiEngine().build(_displayed(action=ActionType.REBALANCE))
    assert exp.expected_outcomes.risk_profile_variance == "DECREASE"
