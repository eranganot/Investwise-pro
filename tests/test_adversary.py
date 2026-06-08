"""Adversary agent tests (Section 6)."""
from app.agents import adversary
from app.schemas.safety import SafetyFlag, SafetyReport


def test_critique_includes_risk_and_confidence():
    c = adversary.critique(path="Growth", risk_critique="Within risk limits",
                           confidence=75.0, impact=32.0)
    assert "Within risk limits" in c
    assert "75%" in c
    assert "Growth" in c


def test_critique_appends_safety_details():
    rep = SafetyReport(verdict="warn", flags=[
        SafetyFlag(type="concentration", severity="medium", detail="X already 40%")])
    c = adversary.critique(path="Bulletproof", risk_critique="ok", confidence=60,
                           impact=20, safety=rep)
    assert "Safety:" in c and "40%" in c


def test_should_veto_only_on_block():
    assert adversary.should_veto(SafetyReport(verdict="block", flags=[])) is True
    assert adversary.should_veto(SafetyReport(verdict="warn", flags=[])) is False
    assert adversary.should_veto(None) is False
