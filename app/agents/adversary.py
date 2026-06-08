"""ADVERSARY agent (Section 6) - mandatory red-team critique on every feed item.

Produces a required risk critique for each displayed item (combining the Risk
Engine's assessment with any Safety flags) and escalates a 'block' safety
verdict into a hard veto - risk/safety override return.
"""
from __future__ import annotations

from app.schemas.safety import SafetyReport


def critique(*, path: str, risk_critique: str, confidence: float,
             impact: float, safety: SafetyReport | None = None) -> str:
    parts = [risk_critique.rstrip(".") + "."] if risk_critique else []
    parts.append(
        f"Confidence {confidence:.0f}% on the {path} path; re-validate if the "
        f"divergence closes or volatility regime shifts."
    )
    if safety and safety.flags:
        parts.append("Safety: " + "; ".join(f.detail for f in safety.flags) + ".")
    return " ".join(parts)


def should_veto(safety: SafetyReport | None) -> bool:
    return bool(safety and safety.verdict == "block")
