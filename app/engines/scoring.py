"""Pure scoring math (Section Z). No I/O - safe to unit test in isolation."""
from __future__ import annotations

from app.schemas.scoring import (
    CONFIDENCE_WEIGHTS,
    IMPACT_WEIGHTS,
    ConfidenceBreakdown,
    ImpactScores,
)


def clamp_score(x: float) -> float:
    """Map any raw value into the strict 0-100 scale."""
    return max(0.0, min(100.0, float(x)))


def compute_impact(scores: ImpactScores, complexity_factor: float) -> float:
    if complexity_factor <= 0:
        raise ValueError("complexity_factor must be > 0")
    weighted = (
        IMPACT_WEIGHTS["return"] * clamp_score(scores.ret)
        + IMPACT_WEIGHTS["tax"] * clamp_score(scores.tax)
        + IMPACT_WEIGHTS["risk"] * clamp_score(scores.risk)
        + IMPACT_WEIGHTS["liquidity"] * clamp_score(scores.liquidity)
        + IMPACT_WEIGHTS["conviction"] * clamp_score(scores.conviction)
    )
    return weighted / complexity_factor


def compute_confidence(b: ConfidenceBreakdown) -> float:
    return (
        CONFIDENCE_WEIGHTS["data_quality"] * clamp_score(b.data_quality)
        + CONFIDENCE_WEIGHTS["model_agreement"] * clamp_score(b.model_agreement)
        + CONFIDENCE_WEIGHTS["historical_accuracy"] * clamp_score(b.historical_accuracy)
        + CONFIDENCE_WEIGHTS["market_stability"] * clamp_score(b.market_stability)
    )
