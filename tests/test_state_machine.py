"""Tests for the Position Lifecycle state machine (Section 1)."""
import pytest
from pydantic import ValidationError

from app.engines.state_machine import StateMachine
from app.schemas.state_machine import (
    ActionType,
    DetectedSignal,
    DisplayedItem,
    Market,
    OptimizedSignal,
    VetoedSignal,
    VettedSignal,
)


def make_signal(divergence: float = 8.0, action: ActionType = ActionType.BUY) -> DetectedSignal:
    return DetectedSignal(
        ticker="TEST",
        market=Market.NYSE,
        action_type=action,
        trigger="unit test",
        depth=3,
        divergence_pct=divergence,
    )


def test_full_linear_pass_reaches_displayed():
    sm = StateMachine()
    result = sm.run(make_signal())
    assert isinstance(result, DisplayedItem)
    assert result.stage.value == "DISPLAYED"
    # Full provenance chain is preserved through every stage.
    assert result.source.source.source.source.ticker == "TEST"
    assert result.path == "Growth"  # BUY -> Growth path


def test_cannot_skip_a_stage():
    """Building an OptimizedSignal directly from a DetectedSignal must fail:
    its ``source`` is typed as VettedSignal, so the stage cannot be skipped."""
    detected = make_signal()
    with pytest.raises(ValidationError):
        OptimizedSignal(source=detected)


def test_veto_blocks_display():
    class VetoRisk:
        def vet(self, signal):
            return VettedSignal(
                source=signal, veto_flag=True, risk_critique="Drawdown cap breached"
            )

    sm = StateMachine(risk=VetoRisk())
    result = sm.run(make_signal())
    assert isinstance(result, VetoedSignal)
    assert "Drawdown" in result.reason


def test_below_threshold_returns_no_action():
    # Low divergence -> low impact score -> below MIN_IMPACT_SCORE (20).
    sm = StateMachine()
    assert sm.run(make_signal(divergence=1.0)) is None


def test_optimize_rejects_vetoed_signal():
    sm = StateMachine()
    vetoed = VettedSignal(source=make_signal(), veto_flag=True)
    with pytest.raises(ValueError):
        sm.optimize(vetoed)


def test_rebalance_uses_bulletproof_path():
    sm = StateMachine()
    result = sm.run(make_signal(action=ActionType.REBALANCE))
    assert isinstance(result, DisplayedItem)
    assert result.path == "Bulletproof"
