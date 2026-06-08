"""Section AF - EXPLAINABILITY ENGINE (XAI).

Turns the fully-typed lifecycle chain behind a DISPLAYED item into a clean,
plain-English justification. No black boxes: every feed item gets a why_now,
supporting/contradicting factors, assumptions, a confidence breakdown, expected
outcomes, and explicit failure (auto-expiry) conditions.
"""
from __future__ import annotations

import hashlib

from app.core.config import Settings, get_settings
from app.schemas.explainability import (
    ConfidenceXAI, ExpectedOutcomes, Explanation,
)
from app.schemas.state_machine import ActionType, DisplayedItem


def recommendation_id(ticker: str, action: str) -> str:
    return "rec_" + hashlib.sha1(f"{ticker}{action}".encode()).hexdigest()[:6]


class XaiEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def build(self, item: DisplayedItem) -> Explanation:
        s = self.settings
        ranked = item.source
        optimized = ranked.source
        vetted = optimized.source
        detected = vetted.source

        supporting: list[str] = []
        if detected.depth == 3:
            supporting.append("Structural Depth-3 backbone signal — high conviction, not surface hype.")
        if vetted.probability_of_ruin is not None and vetted.probability_of_ruin < 0.10:
            supporting.append(f"Low probability of ruin ({vetted.probability_of_ruin:.0%}) in Monte Carlo stress.")
        if optimized.tax_saved:
            supporting.append(f"Tax-efficient: ~₪{optimized.tax_saved:,.0f} saved via loss offset.")
        supporting.append(f"Return sub-score {ranked.scores.ret:.0f}/100; impact {ranked.impact_score:.0f}.")

        contradicting: list[str] = []
        if detected.volatility_pct and detected.volatility_pct > s.volatility_cap * 100:
            contradicting.append(f"Volatility {detected.volatility_pct:.0f}% sits above the "
                                 f"{s.volatility_cap:.0%} comfort band.")
        if optimized.actual_tax_cost:
            contradicting.append(f"Crystallizes ~₪{optimized.actual_tax_cost:,.0f} of tax now.")
        if ranked.complexity_label in ("Difficult", "Complex"):
            contradicting.append(f"Execution complexity rated {ranked.complexity_label}.")
        if not contradicting:
            contradicting.append("Short-term transaction costs may be slightly elevated this session.")

        assumptions = [
            "Underlying liquidity remains below the 48-hour liquidation threshold.",
            "Quoted spot and listing prices are current as of this pipeline run.",
        ]

        net = optimized.net_gain_delta
        variance = "DECREASE" if item.path == "Bulletproof" else "NEUTRAL"
        if detected.action_type == ActionType.RISK:
            variance = "DECREASE"

        failure_conditions = [
            f"If the {detected.market.value} divergence closes back inside the "
            f"{s.lag_min_divergence_pct:.1f}% structural-lag threshold before manual approval, "
            "this recommendation is stale and must auto-expire.",
            f"If realized volatility breaches the {s.volatility_cap:.0%} cap, the Risk Engine "
            "vetoes it on the next run.",
        ]

        return Explanation(
            recommendation_id=recommendation_id(detected.ticker, detected.action_type.value),
            why_now=f"{detected.trigger}. Impact {ranked.impact_score:.0f} on the {item.path} path "
                    f"with {ranked.confidence:.0f}% confidence.",
            supporting_factors=supporting,
            contradicting_factors=contradicting,
            assumptions=assumptions,
            confidence_breakdown=ConfidenceXAI(
                confidence=round(ranked.confidence),
                components=ranked.confidence_breakdown.model_dump(),
            ),
            expected_outcomes=ExpectedOutcomes(
                net_wealth_delta_currency=(round(net, 2) if net is not None else None),
                risk_profile_variance=variance,
            ),
            failure_conditions=failure_conditions,
        )
