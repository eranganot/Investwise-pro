"""4.2 LAG ENGINE - Depth 1-3 mapping, Spot vs TASE/NYSE divergence.

Phase 0: a thin constructor that turns raw inputs into a DetectedSignal.
Real divergence detection + Backbone-vs-Hype ranking arrives in Phase 3.
"""
from __future__ import annotations

from app.schemas.state_machine import ActionType, DetectedSignal, Market


class LagEngine:
    def detect(
        self,
        *,
        ticker: str,
        market: Market,
        action_type: ActionType,
        trigger: str,
        depth: int,
        divergence_pct: float,
    ) -> DetectedSignal:
        return DetectedSignal(
            ticker=ticker,
            market=market,
            action_type=action_type,
            trigger=trigger,
            depth=depth,
            divergence_pct=divergence_pct,
            notes="Awaiting Data - Lag depth analysis not yet implemented (Phase 3).",
        )
