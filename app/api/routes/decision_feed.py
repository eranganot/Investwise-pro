"""Decision Feed routes - the full Lag -> Risk -> Tax -> Decision pipeline.

- GET  /decision-feed/demo      : in-memory run (no DB), quick view
- POST /decision-feed/generate  : run + PERSIST feed/items to Postgres (Section 4)
- GET  /decision-feed/latest    : read back the most recent persisted feed
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_session
from app.engines.lag_engine import LagEngine
from app.engines.risk_engine import RiskEngine
from app.engines.state_machine import StateMachine
from app.models.tables import DecisionFeed, DecisionItem
from app.schemas.lag import LagObservation
from app.schemas.state_machine import ActionType, DisplayedItem, Market, VetoedSignal
from app.services.feed_service import build_recommendation, ensure_superadmin

router = APIRouter(prefix="/api/v1", tags=["decision-feed"])

DEFAULT_OBSERVATIONS = [
    LagObservation(ticker="TEVA", market=Market.NYSE, depth=3, spot_price=100,
                   listing_price=108.2, expected_return_pct=10, volatility_pct=12,
                   action_type=ActionType.BUY),
    LagObservation(ticker="HYPE", market=Market.NYSE, depth=1, spot_price=100,
                   listing_price=112, expected_return_pct=15, volatility_pct=40,
                   action_type=ActionType.BUY),
    LagObservation(ticker="GOLD", market=Market.SPOT, depth=1, spot_price=100,
                   listing_price=103.1, expected_return_pct=6, volatility_pct=8,
                   action_type=ActionType.REBALANCE),
    LagObservation(ticker="NOISE", market=Market.TASE, depth=1, spot_price=100,
                   listing_price=100.6, action_type=ActionType.BUY),
]


def _machine() -> tuple[LagEngine, StateMachine]:
    return LagEngine(), StateMachine(risk=RiskEngine(seed=7))


@router.get("/decision-feed/demo")
async def demo_feed() -> dict:
    lag, sm = _machine()
    items = []
    for s in lag.scan(DEFAULT_OBSERVATIONS):
        result = sm.run(s)
        if result is None:
            items.append({"ticker": s.ticker, "depth": s.depth, "decision": "No Action Recommended"})
        elif isinstance(result, VetoedSignal):
            items.append({"ticker": s.ticker, "depth": s.depth, "decision": "VETOED",
                          "reason": result.reason,
                          "prob_of_ruin": round(result.source.probability_of_ruin, 3)
                          if result.source.probability_of_ruin is not None else None})
        elif isinstance(result, DisplayedItem):
            ranked = result.source
            vetted = ranked.source.source
            items.append({"ticker": s.ticker, "depth": s.depth, "title": result.title,
                          "path": result.path, "stage": result.stage.value,
                          "impact_score": round(ranked.impact_score, 1),
                          "confidence": ranked.confidence,
                          "r_score": round(ranked.r_score, 1),
                          "t_score": round(ranked.t_score, 1),
                          "risk_score": round(ranked.risk_score, 1),
                          "prob_of_ruin": round(vetted.probability_of_ruin, 3)
                          if vetted.probability_of_ruin is not None else None,
                          "max_drawdown": round(vetted.max_drawdown, 3)
                          if vetted.max_drawdown is not None else None})
    return {"generated": "demo (Lag-driven pipeline)",
            "backbone_vs_hype": lag.backbone_vs_hype(DEFAULT_OBSERVATIONS),
            "count": len(items), "items": items}


class GenerateRequest(BaseModel):
    observations: list[LagObservation] | None = None


@router.post("/decision-feed/generate")
async def generate_feed(
    req: GenerateRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    observations = (req.observations if req and req.observations else DEFAULT_OBSERVATIONS)
    lag, sm = _machine()
    user = await ensure_superadmin(session)
    feed = DecisionFeed(user_id=user.id, horizon="month", status="OPEN")
    session.add(feed)
    await session.flush()

    out = []
    for s in lag.scan(observations):
        result = sm.run(s)
        if isinstance(result, DisplayedItem):
            rec = build_recommendation(result)
            session.add(DecisionItem(
                feed_id=feed.id, title=rec.title, action_type=rec.action_type,
                trigger=rec.trigger, execution_plan=rec.execution_plan,
                impact_score=rec.impact_score, confidence=rec.confidence,
                urgency=rec.urgency, complexity=rec.complexity,
                time_sensitivity=rec.time_sensitivity, veto_flag=False,
                risk_critique=rec.risk_critique, payload=rec.model_dump(),
            ))
            out.append({"ticker": s.ticker, "title": rec.title, "path": result.path,
                        "impact_score": rec.impact_score, "time_sensitivity": rec.time_sensitivity})
        elif isinstance(result, VetoedSignal):
            session.add(DecisionItem(
                feed_id=feed.id, title=f"{s.action_type.value} {s.ticker}",
                action_type=s.action_type.value, trigger=s.trigger,
                impact_score=0.0, confidence=0.0, veto_flag=True,
                time_sensitivity="Monitor", risk_critique=result.reason,
                payload={"decision": "VETOED", "reason": result.reason},
            ))
            out.append({"ticker": s.ticker, "decision": "VETOED"})
    await session.commit()
    return {"feed_id": str(feed.id), "user": user.email,
            "persisted_items": len(out), "items": out}


@router.get("/decision-feed/latest")
async def latest_feed(session: AsyncSession = Depends(get_session)) -> dict:
    res = await session.execute(
        select(DecisionFeed).options(selectinload(DecisionFeed.items))
        .order_by(desc(DecisionFeed.generated_at)).limit(1)
    )
    feed = res.scalar_one_or_none()
    if feed is None:
        return {"feed": None, "message": "No feeds persisted yet - POST /api/v1/decision-feed/generate first."}
    return {
        "feed_id": str(feed.id),
        "generated_at": feed.generated_at.isoformat() if feed.generated_at else None,
        "status": feed.status,
        "item_count": len(feed.items),
        "items": [{
            "title": i.title, "action_type": i.action_type,
            "impact_score": i.impact_score, "confidence": i.confidence,
            "veto_flag": i.veto_flag, "time_sensitivity": i.time_sensitivity,
            "risk_critique": i.risk_critique,
        } for i in feed.items],
    }
