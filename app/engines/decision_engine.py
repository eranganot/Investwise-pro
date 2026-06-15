"""4.5 / Section Z DECISION ENGINE - 5-component Impact + 4-component Confidence.

Impact = (0.30 R + 0.25 Tax + 0.25 Risk + 0.10 Liquidity + 0.10 Conviction)
         / Complexity_Factor
Confidence = 0.40 data_quality + 0.30 model_agreement + 0.20 historical_accuracy
           + 0.10 market_stability   (with full breakdown)
All sub-scores are normalized to 0-100 before combination.
"""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.engines.scoring import clamp_score, compute_confidence, compute_impact
from app.schemas.scoring import COMPLEXITY_FACTOR, IMPACT_WEIGHTS, Complexity, ConfidenceBreakdown, ImpactScores
from app.schemas.state_machine import ActionType, OptimizedSignal, RankedSignal

COMPLEXITY_BY_ACTION = {
    ActionType.BUY: Complexity.EASY,
    ActionType.SELL: Complexity.MODERATE,
    ActionType.REBALANCE: Complexity.MODERATE,
    ActionType.TAX: Complexity.DIFFICULT,
    ActionType.RISK: Complexity.MODERATE,
}


class DecisionEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def rank(self, signal: OptimizedSignal, *, historical_accuracy: float = 70.0) -> RankedSignal:
        vetted = signal.source
        detected = vetted.source

        # --- Impact sub-scores (0-100) ---
        # Honesty rule (Phase C): only score dimensions we actually have data for.
        # An unknown dimension is treated as NEUTRAL (the weighted average of what we
        # DO know), never a fabricated 25-point penalty - so missing data lowers
        # confidence (via data_quality below), not the impact itself.
        ret = clamp_score(max(detected.expected_return_pct or 0.0, abs(detected.divergence_pct)) * self.settings.decision_return_scale)
        conviction = clamp_score(detected.depth / 3.0 * 100.0)
        if self.settings.preferred_depth and detected.depth == self.settings.preferred_depth:
            conviction = clamp_score(conviction + 25.0)  # plan 'flavor' favors this depth
        known: dict[str, float] = {"return": ret, "conviction": conviction}
        if signal.net_gain_delta is not None and detected.gross_gain_ils:
            known["tax"] = clamp_score(signal.net_gain_delta / detected.gross_gain_ils * 100.0)
        if vetted.probability_of_ruin is not None:
            known["risk"] = clamp_score((1.0 - vetted.probability_of_ruin) * 100.0)
        if detected.liquidity_score is not None:
            known["liquidity"] = clamp_score(detected.liquidity_score)
        _wsum = sum(IMPACT_WEIGHTS[k] for k in known) or 1.0
        neutral = sum(IMPACT_WEIGHTS[k] * v for k, v in known.items()) / _wsum
        scores = ImpactScores(ret=ret, conviction=conviction,
                              tax=known.get("tax", neutral),
                              risk=known.get("risk", neutral),
                              liquidity=known.get("liquidity", neutral))
        risk = scores.risk  # for the model-agreement heuristic below

        label = COMPLEXITY_BY_ACTION.get(detected.action_type, Complexity.MODERATE)
        factor = COMPLEXITY_FACTOR[label]
        impact = compute_impact(scores, factor)

        # --- Confidence breakdown (0-100) ---
        vol = detected.volatility_pct
        s = self.settings
        data_quality = s.confidence_dq_base + (s.confidence_dq_bonus if vol is not None else 0.0) \
            + (s.confidence_dq_bonus if detected.gross_gain_ils else 0.0)
        model_agreement = s.confidence_conflict_agreement if (ret >= 60 and risk < 40) else s.confidence_model_agreement
        market_stability = clamp_score(100.0 - max(0.0, (vol if vol is not None else 10.0) - 10.0) * 2.0) \
            if vol is not None else 70.0
        breakdown = ConfidenceBreakdown(
            data_quality=clamp_score(data_quality), model_agreement=model_agreement,
            historical_accuracy=clamp_score(historical_accuracy), market_stability=market_stability,
        )
        confidence = compute_confidence(breakdown)

        urgency = int(max(1.0, min(100.0, round(impact * 1.5))))
        return RankedSignal(
            source=signal, impact_score=impact, confidence=confidence,
            scores=scores, confidence_breakdown=breakdown,
            complexity_label=label.value, complexity_factor=factor, urgency=urgency,
        )
