"""Phase 1.2 - strict cross-agent validation guards.

These prove that malformed state can never cross an agent boundary: NaN/Inf,
out-of-range probabilities/scores, unexpected fields, an unexplained veto, or a
wrong-stage hand-off all raise instead of propagating silently.
"""
from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from app.schemas.scoring import ConfidenceBreakdown, ImpactScores
from app.schemas.state_machine import (
    ActionType,
    DetectedSignal,
    Market,
    OptimizedSignal,
    RankedSignal,
    VettedSignal,
)
from app.schemas.validation import HandoffError, assert_handoff


def _detected(**kw) -> DetectedSignal:
    base = dict(ticker="TEVA", market=Market.NYSE, action_type=ActionType.BUY,
                trigger="t", depth=3, divergence_pct=8.0)
    base.update(kw)
    return DetectedSignal(**base)


def test_detected_baseline_ok():
    assert _detected().divergence_pct == 8.0


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_nan_inf_rejected_on_divergence(bad):
    with pytest.raises(ValidationError):
        _detected(divergence_pct=bad)


def test_negative_volatility_rejected():
    with pytest.raises(ValidationError):
        _detected(volatility_pct=-5.0)


def test_liquidity_score_out_of_range_rejected():
    with pytest.raises(ValidationError):
        _detected(liquidity_score=140.0)


def test_unexpected_field_rejected():
    with pytest.raises(ValidationError):
        _detected(typo_field=1)


def test_string_not_coerced_in_strict_mode():
    # strict mode: a stringified number must not silently coerce.
    with pytest.raises(ValidationError):
        _detected(divergence_pct="8.0")


def test_probability_of_ruin_must_be_unit_fraction():
    det = _detected()
    with pytest.raises(ValidationError):
        VettedSignal(source=det, probability_of_ruin=1.5)


def test_veto_requires_reason():
    det = _detected()
    with pytest.raises(ValidationError):
        VettedSignal(source=det, veto_flag=True, risk_critique="   ")


def test_veto_with_reason_ok():
    det = _detected()
    v = VettedSignal(source=det, veto_flag=True, risk_critique="vol too high")
    assert v.veto_flag and v.risk_critique


def test_impact_subscore_bounds():
    with pytest.raises(ValidationError):
        ImpactScores(ret=120, tax=0, risk=0, liquidity=0, conviction=0)


def test_confidence_component_bounds():
    with pytest.raises(ValidationError):
        ConfidenceBreakdown(data_quality=-1, model_agreement=0,
                            historical_accuracy=0, market_stability=0)


def test_frozen_stage_is_immutable():
    det = _detected()
    with pytest.raises(ValidationError):
        det.divergence_pct = 9.0


def test_assert_handoff_passes_for_correct_type():
    det = _detected()
    assert assert_handoff(det, DetectedSignal) is det


def test_assert_handoff_rejects_wrong_stage():
    det = _detected()
    with pytest.raises(HandoffError):
        assert_handoff(det, VettedSignal)
