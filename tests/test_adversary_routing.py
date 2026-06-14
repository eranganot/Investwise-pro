"""Phase 1.3 - Adversary per-stage cross-examination."""
from __future__ import annotations

import pytest

from app.agents.adversary import Adversary, Severity
from app.core.config import get_settings
from app.engines.risk_engine import RiskEngine
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


def _strong_buy() -> DetectedSignal:
    # A signal that should clear the display gate end-to-end.
    return DetectedSignal(
        ticker="TEVA", market=Market.NYSE, action_type=ActionType.BUY,
        trigger="Depth 3 backbone divergence", depth=3, divergence_pct=8.0,
        expected_return_pct=12.0, volatility_pct=14.0,
        gross_gain_ils=120_000.0, liquidity_score=85.0,
    )


def _sm() -> StateMachine:
    return StateMachine(risk=RiskEngine(seed=7))


def test_every_stage_is_examined_on_a_clean_run():
    exam = _sm().cross_examine(_strong_buy())
    stages = [n.stage for n in exam.notes]
    assert stages == ["Detected", "Vetted", "Optimized", "Ranked"]
    assert all(n.severity == Severity.OK for n in exam.notes)
    assert isinstance(exam.outcome, DisplayedItem)


def test_examiner_flags_implausible_sharpe():
    adv = Adversary()
    det = _strong_buy().model_copy(update={"expected_return_pct": 60.0, "volatility_pct": 10.0})
    note = adv.examine_detected(det)
    assert note.severity == Severity.WARN
    assert any("return/volatility" in f for f in note.findings)


def test_examiner_blocks_net_exceeding_gross():
    adv = Adversary()
    det = _strong_buy()
    opt = OptimizedSignal(source=VettedSignal(source=det), net_gain_delta=999_999.0)
    note = adv.examine_optimized(opt)
    assert note.blocks
    assert any("exceeds gross gain" in f for f in note.findings)


class _BadTax:
    """A tax engine that produces an impossible net-after-tax (blind spot)."""
    def optimize(self, signal: VettedSignal) -> OptimizedSignal:
        gross = signal.source.gross_gain_ils or 0.0
        return OptimizedSignal(source=signal, net_gain_delta=gross * 5 + 1.0)


def test_enforcement_converts_block_into_veto():
    sm = StateMachine(risk=RiskEngine(seed=7), tax=_BadTax())
    assert sm.settings.adversary_enforce_veto is True
    exam = sm.cross_examine(_strong_buy())
    assert isinstance(exam.outcome, VetoedSignal)
    assert "ADVERSARY" in exam.outcome.reason
    assert exam.notes[-1].blocks


def test_llm_narrative_off_by_default_returns_none():
    adv = Adversary()
    assert adv.settings.adversary_llm_enabled is False
    notes = [adv.examine_detected(_strong_buy())]
    assert adv.narrate(notes) is None  # disabled -> deterministic-only, no LLM call


def test_war_room_includes_cross_examination_lines():
    from app.services.demo_data import DEFAULT_OBSERVATIONS
    from app.services.war_room import build_war_room
    wr = build_war_room(DEFAULT_OBSERVATIONS)
    teva = [s for s in wr["sessions"] if s["ticker"] == "TEVA"][0]
    xexam = [l for l in teva["transcript"]
             if l["agent"] == "Adversary" and "cross-examination" in l["role"]]
    assert xexam, "expected Adversary cross-examination lines in the transcript"
    assert all("severity" in l["detail"] for l in xexam)


def test_llm_narrative_uses_google_key_gate(monkeypatch):
    from app.agents.adversary import Adversary
    from app.core.config import get_settings
    s = get_settings().model_copy(update={"adversary_llm_enabled": True})
    adv = Adversary(s)
    notes = [adv.examine_detected(_strong_buy())]
    # enabled but no Google key -> still None (no call attempted)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-be-ignored")
    assert adv.narrate(notes) is None


def test_war_room_omits_ai_narrative_when_disabled():
    from app.services.demo_data import DEFAULT_OBSERVATIONS
    from app.services.war_room import build_war_room
    wr = build_war_room(DEFAULT_OBSERVATIONS)  # LLM off by default -> narrate() returns None
    for sess in wr["sessions"]:
        assert not any("AI narrative" in l["role"] for l in sess["transcript"])


def test_war_room_includes_ai_narrative_when_enabled(monkeypatch):
    from app.agents.adversary import Adversary
    from app.services.demo_data import DEFAULT_OBSERVATIONS
    from app.services.war_room import build_war_room
    # simulate Gemini returning a narrative, without any network call
    monkeypatch.setattr(Adversary, "narrate", lambda self, notes, context="": "AI: I challenge this thesis.")
    wr = build_war_room(DEFAULT_OBSERVATIONS)
    teva = [s for s in wr["sessions"] if s["ticker"] == "TEVA"][0]
    ai = [l for l in teva["transcript"] if l["role"].endswith("AI narrative")]
    assert ai and ai[0]["says"].startswith("AI:") and ai[0]["detail"]["source"] == "gemini"
