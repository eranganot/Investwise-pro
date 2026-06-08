"""4.2 LAG ENGINE - structural lag detection (Phase 3).

Monitors spot price vs. TASE/NYSE listing divergence and maps each signal to a
Depth (1 = surface "hype" ... 3 = structural "backbone"). Per the ALPHA mandate
the engine prioritizes Depth 3: a backbone divergence outranks a larger surface
divergence. Tiny divergences below the configurable noise floor are ignored.
"""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.schemas.lag import LagObservation
from app.schemas.state_machine import DetectedSignal


class LagEngine:
    # ALPHA: structural backbone (Depth 3) weighted above surface hype (Depth 1).
    DEPTH_WEIGHTS = {1: 1.0, 2: 1.5, 3: 2.0}
    DEPTH_LABEL = {1: "hype", 2: "mid", 3: "backbone"}

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @staticmethod
    def divergence_pct(spot_price: float, listing_price: float) -> float:
        """Listing vs. spot reference; positive = listing trades above spot."""
        return (listing_price - spot_price) / spot_price * 100.0

    def depth_weight(self, depth: int) -> float:
        return self.DEPTH_WEIGHTS.get(depth, 1.0)

    def priority(self, divergence_pct: float, depth: int) -> float:
        """ALPHA priority = divergence magnitude boosted by structural depth."""
        return abs(divergence_pct) * self.depth_weight(depth)

    def detect(self, obs: LagObservation) -> DetectedSignal | None:
        div = self.divergence_pct(obs.spot_price, obs.listing_price)
        if abs(div) < self.settings.lag_min_divergence_pct:
            return None  # below the noise floor
        trigger = (
            f"Depth {obs.depth} {self.DEPTH_LABEL.get(obs.depth, '')} divergence "
            f"{div:+.1f}% ({obs.market.value} vs spot)"
        )
        return DetectedSignal(
            ticker=obs.ticker,
            market=obs.market,
            action_type=obs.action_type,
            trigger=trigger,
            depth=obs.depth,
            divergence_pct=div,
            expected_return_pct=obs.expected_return_pct,
            volatility_pct=obs.volatility_pct,
        )

    def scan(self, observations: list[LagObservation]) -> list[DetectedSignal]:
        """Detect divergences and rank by ALPHA priority (Depth 3 first)."""
        detected = [s for s in (self.detect(o) for o in observations) if s is not None]
        detected.sort(key=lambda s: self.priority(s.divergence_pct, s.depth), reverse=True)
        return detected

    def backbone_vs_hype(self, observations: list[LagObservation]) -> float | None:
        """Ratio of structural (Depth 3) to surface (Depth 1) divergence magnitude.

        >1 means the move is backbone-led (higher conviction); None if there's no
        Depth 1 baseline to compare against.
        """
        backbone = sum(
            abs(self.divergence_pct(o.spot_price, o.listing_price))
            for o in observations if o.depth == 3
        )
        hype = sum(
            abs(self.divergence_pct(o.spot_price, o.listing_price))
            for o in observations if o.depth == 1
        )
        return backbone / hype if hype else None
