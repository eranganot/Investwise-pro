"""Demo Decision Feed - the full Lag -> Risk -> Tax -> Decision pipeline.

Phase 3: signals now originate from the Lag Engine scanning spot-vs-listing
observations. Depth 3 (backbone) outranks Depth 1 (hype); sub-noise-floor moves
(NOISE) are filtered before they ever enter the lifecycle.
"""
from fastapi import APIRouter

from app.engines.lag_engine import LagEngine
from app.engines.risk_engine import RiskEngine
from app.engines.state_machine import StateMachine
from app.schemas.lag import LagObservation
from app.schemas.state_machine import ActionType, DisplayedItem, Market, VetoedSignal

router = APIRouter(prefix="/api/v1", tags=["decision-feed"])


@router.get("/decision-feed/demo")
async def demo_feed() -> dict:
    lag = LagEngine()
    sm = StateMachine(risk=RiskEngine(seed=7))  # seeded for stable demo output

    observations = [
        LagObservation(ticker="TEVA", market=Market.NYSE, depth=3,
                       spot_price=100, listing_price=108.2,
                       expected_return_pct=10, volatility_pct=12,
                       action_type=ActionType.BUY),
        LagObservation(ticker="HYPE", market=Market.NYSE, depth=1,
                       spot_price=100, listing_price=112,
                       expected_return_pct=15, volatility_pct=40,
                       action_type=ActionType.BUY),
        LagObservation(ticker="GOLD", market=Market.SPOT, depth=1,
                       spot_price=100, listing_price=103.1,
                       expected_return_pct=6, volatility_pct=8,
                       action_type=ActionType.REBALANCE),
        LagObservation(ticker="NOISE", market=Market.TASE, depth=1,
                       spot_price=100, listing_price=100.6,
                       action_type=ActionType.BUY),
    ]

    signals = lag.scan(observations)  # ranked; NOISE filtered out
    items = []
    for s in signals:
        result = sm.run(s)
        if result is None:
            items.append({"ticker": s.ticker, "depth": s.depth,
                          "decision": "No Action Recommended"})
        elif isinstance(result, VetoedSignal):
            items.append({
                "ticker": s.ticker, "depth": s.depth, "decision": "VETOED",
                "reason": result.reason,
                "prob_of_ruin": round(result.source.probability_of_ruin, 3)
                if result.source.probability_of_ruin is not None else None,
            })
        elif isinstance(result, DisplayedItem):
            vetted = result.source.source.source
            items.append({
                "ticker": s.ticker, "depth": s.depth,
                "title": result.title, "path": result.path,
                "stage": result.stage.value,
                "impact_score": round(result.source.impact_score, 1),
                "confidence": result.source.confidence,
                "prob_of_ruin": round(vetted.probability_of_ruin, 3)
                if vetted.probability_of_ruin is not None else None,
                "max_drawdown": round(vetted.max_drawdown, 3)
                if vetted.max_drawdown is not None else None,
            })

    return {
        "generated": "demo (Lag-driven pipeline)",
        "backbone_vs_hype": lag.backbone_vs_hype(observations),
        "count": len(items),
        "items": items,
    }
