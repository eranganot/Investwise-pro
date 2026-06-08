"""Demo Decision Feed - runs the full lifecycle on sample signals.

Now exercises the Risk Engine (Phase 2): high-volatility names are vetoed
regardless of ROI ("risk overrides return"). Risk uses a fixed seed here so
the demo output is stable across reloads.
"""
from fastapi import APIRouter

from app.engines.risk_engine import RiskEngine
from app.engines.state_machine import StateMachine
from app.schemas.state_machine import (
    ActionType,
    DetectedSignal,
    DisplayedItem,
    Market,
    VetoedSignal,
)

router = APIRouter(prefix="/api/v1", tags=["decision-feed"])


@router.get("/decision-feed/demo")
async def demo_feed() -> dict:
    sm = StateMachine(risk=RiskEngine(seed=7))
    signals = [
        DetectedSignal(
            ticker="TEVA", market=Market.NYSE, action_type=ActionType.BUY,
            trigger="Depth 3 backbone divergence vs TASE listing",
            depth=3, divergence_pct=8.2,
            expected_return_pct=10.0, volatility_pct=12.0,
        ),
        DetectedSignal(
            ticker="HYPE", market=Market.NYSE, action_type=ActionType.BUY,
            trigger="Momentum spike (high ROI, high risk)",
            depth=2, divergence_pct=12.0,
            expected_return_pct=15.0, volatility_pct=40.0,
        ),
        DetectedSignal(
            ticker="GOLD", market=Market.SPOT, action_type=ActionType.REBALANCE,
            trigger="Commodity spot delta vs allocation target",
            depth=1, divergence_pct=3.1,
            expected_return_pct=6.0, volatility_pct=8.0,
        ),
        DetectedSignal(
            ticker="NOISE", market=Market.TASE, action_type=ActionType.BUY,
            trigger="Marginal Depth 1 wiggle",
            depth=1, divergence_pct=0.6,
        ),
    ]

    items = []
    for s in signals:
        result = sm.run(s)
        if result is None:
            items.append({"ticker": s.ticker, "decision": "No Action Recommended"})
        elif isinstance(result, VetoedSignal):
            items.append({
                "ticker": s.ticker,
                "decision": "VETOED",
                "reason": result.reason,
                "prob_of_ruin": round(result.source.probability_of_ruin, 3)
                if result.source.probability_of_ruin is not None else None,
            })
        elif isinstance(result, DisplayedItem):
            vetted = result.source.source.source  # Ranked->Optimized->Vetted
            items.append({
                "ticker": s.ticker,
                "title": result.title,
                "path": result.path,
                "stage": result.stage.value,
                "impact_score": round(result.source.impact_score, 1),
                "confidence": result.source.confidence,
                "prob_of_ruin": round(vetted.probability_of_ruin, 3)
                if vetted.probability_of_ruin is not None else None,
                "max_drawdown": round(vetted.max_drawdown, 3)
                if vetted.max_drawdown is not None else None,
            })
    return {"generated": "demo", "count": len(items), "items": items}
