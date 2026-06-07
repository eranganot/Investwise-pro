"""Demo Decision Feed - runs the lifecycle on sample signals (Phase 0).

Real feeds (persisted, multi-entity) come once the engines are live.
"""
from fastapi import APIRouter

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
    sm = StateMachine()
    signals = [
        DetectedSignal(
            ticker="TEVA",
            market=Market.NYSE,
            action_type=ActionType.BUY,
            trigger="Depth 3 backbone divergence vs TASE listing",
            depth=3,
            divergence_pct=8.2,
        ),
        DetectedSignal(
            ticker="GOLD",
            market=Market.SPOT,
            action_type=ActionType.REBALANCE,
            trigger="Commodity spot delta vs allocation target",
            depth=1,
            divergence_pct=3.1,
        ),
        DetectedSignal(
            ticker="NOISE",
            market=Market.TASE,
            action_type=ActionType.BUY,
            trigger="Marginal Depth 1 wiggle",
            depth=1,
            divergence_pct=0.6,
        ),
    ]

    items = []
    for s in signals:
        result = sm.run(s)
        if result is None:
            items.append(
                {"ticker": s.ticker, "decision": "No Action Recommended"}
            )
        elif isinstance(result, VetoedSignal):
            items.append(
                {"ticker": s.ticker, "decision": "VETOED", "reason": result.reason}
            )
        elif isinstance(result, DisplayedItem):
            items.append(
                {
                    "ticker": s.ticker,
                    "title": result.title,
                    "path": result.path,
                    "stage": result.stage.value,
                    "impact_score": round(result.source.impact_score, 1),
                    "confidence": result.source.confidence,
                    "note": "Phase 0 stub engines - metrics are placeholders "
                    "('Awaiting Data').",
                }
            )
    return {"generated": "demo", "count": len(items), "items": items}
