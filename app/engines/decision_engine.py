"""4.5 DECISION ENGINE - Impact Score + confidence (display gates).

Impact Score = (0.4*R + 0.3*T + 0.3*Risk) / Complexity  [Phase 4].
Phase 0 stub: derives a placeholder impact from the Lag divergence so the
pipeline produces a visible item, and a fixed confidence.
"""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.schemas.state_machine import OptimizedSignal, RankedSignal


class DecisionEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def rank(self, signal: OptimizedSignal) -> RankedSignal:
        detected = signal.source.source  # Optimized -> Vetted -> Detected
        # Placeholder proxy until the real weighted formula lands in Phase 4.
        impact = min(abs(detected.divergence_pct) * 5.0, 100.0)
        return RankedSignal(
            source=signal,
            impact_score=impact,
            confidence=65.0,
            complexity=2,
            urgency=50,
        )
