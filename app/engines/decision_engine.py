"""4.5 DECISION ENGINE - Impact Score + confidence (display gates).

Impact Score = (0.4*R + 0.3*T + 0.3*Risk) / Complexity

  R    (return)  - normalized opportunity: max(expected return, |divergence|),
                   so a Lag divergence alone still scores when no return is given.
  T    (tax)     - net-after-tax retention (net_gain_delta / gross gain); the
                   spec names net_gain_delta the primary tax input. 50 if unknown.
  Risk (safety)  - (1 - probability_of_ruin) * 100; higher = safer. 50 if unknown.

Confidence rises with Lag depth (backbone conviction) and data completeness.
Display gates: Impact >= min_impact_score and Confidence >= min_confidence.
"""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.schemas.state_machine import OptimizedSignal, RankedSignal

W_RETURN, W_TAX, W_RISK = 0.4, 0.3, 0.3


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


class DecisionEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def rank(self, signal: OptimizedSignal) -> RankedSignal:
        vetted = signal.source
        detected = vetted.source

        # R - return / opportunity
        ret_pct = detected.expected_return_pct or 0.0
        r_score = _clamp(max(ret_pct, abs(detected.divergence_pct)) * 5.0)

        # T - tax efficiency (after-tax retention)
        if signal.net_gain_delta is not None and detected.gross_gain_ils:
            t_score = _clamp(signal.net_gain_delta / detected.gross_gain_ils * 100.0)
        else:
            t_score = 50.0  # neutral when no tax economics provided

        # Risk - safety (inverse of ruin probability)
        if vetted.probability_of_ruin is not None:
            risk_score = _clamp((1.0 - vetted.probability_of_ruin) * 100.0)
        else:
            risk_score = 50.0  # neutral when not stress-tested

        complexity = 2  # medium default; per-action complexity is a later refinement
        impact = (W_RETURN * r_score + W_TAX * t_score + W_RISK * risk_score) / complexity

        # Confidence: depth conviction + data completeness
        confidence = 40.0 + 20.0 * (detected.depth / 3.0)
        if vetted.probability_of_ruin is not None:
            confidence += 15.0
        if signal.net_gain_delta is not None:
            confidence += 15.0
        confidence = _clamp(confidence)

        urgency = int(_clamp(round(impact * 1.5), 1, 100))

        return RankedSignal(
            source=signal,
            impact_score=impact,
            confidence=confidence,
            complexity=complexity,
            urgency=urgency,
            r_score=r_score,
            t_score=t_score,
            risk_score=risk_score,
        )
