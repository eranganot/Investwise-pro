"""Decision Feed routes.

- GET  /decision-feed/demo      : in-memory run (no DB), every item carries an Adversary critique
- POST /decision-feed/generate  : run + persist (delegates to PipelineOrchestrator)
- GET  /decision-feed/latest    : read back the most recent persisted feed (scoped to the user)
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents import adversary
from app.api.deps import acting_user
from app.core.auth import Role, require_role
from app.core.database import get_session
from app.engines.lag_engine import LagEngine
from app.engines.risk_engine import RiskEngine
from app.engines.state_machine import StateMachine
from app.engines.xai_engine import XaiEngine
from app.models.tables import DecisionFeed, User
from app.schemas.feed import GenerateRequest
from app.schemas.state_machine import DisplayedItem, VetoedSignal
from app.services.demo_data import DEFAULT_OBSERVATIONS
from app.services.pipeline import PipelineOrchestrator

router = APIRouter(prefix="/api/v1", tags=["decision-feed"])


def _is_stale(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        return datetime.now(timezone.utc) > datetime.fromisoformat(expires_at)
    except ValueError:
        return False


def _machine():
    return LagEngine(), StateMachine(risk=RiskEngine(seed=7))


@router.get("/decision-feed/demo")
async def demo_feed() -> dict:
    lag, sm = _machine()
    items = []
    for s in lag.scan(DEFAULT_OBSERVATIONS):
        result = sm.run(s)
        if result is None:
            items.append({"ticker": s.ticker, "depth": s.depth, "decision": "No Action Recommended",
                          "adversary_critique": "Below the Impact/Confidence quality bar - holding."})
        elif isinstance(result, VetoedSignal):
            items.append({"ticker": s.ticker, "depth": s.depth, "decision": "VETOED",
                          "reason": result.reason, "adversary_critique": result.reason,
                          "prob_of_ruin": round(result.source.probability_of_ruin, 3)
                          if result.source.probability_of_ruin is not None else None})
        elif isinstance(result, DisplayedItem):
            ranked = result.source
            vetted = ranked.source.source
            crit = adversary.critique(path=result.path, risk_critique=vetted.risk_critique,
                                      confidence=ranked.confidence, impact=ranked.impact_score)
            items.append({"ticker": s.ticker, "depth": s.depth, "title": result.title,
                          "path": result.path, "stage": result.stage.value,
                          "impact_score": round(ranked.impact_score, 1),
                          "confidence": round(ranked.confidence, 1),
                          "complexity": ranked.complexity_label,
                          "scores": ranked.scores.as_contract(),
                          "confidence_breakdown": ranked.confidence_breakdown.model_dump(),
                          "prob_of_ruin": round(vetted.probability_of_ruin, 3)
                          if vetted.probability_of_ruin is not None else None,
                          "max_drawdown": round(vetted.max_drawdown, 3)
                          if vetted.max_drawdown is not None else None,
                          "adversary_critique": crit,
                          "explanation": XaiEngine().build(result).model_dump()})
    return {"generated": "demo (Lag-driven pipeline)",
            "backbone_vs_hype": lag.backbone_vs_hype(DEFAULT_OBSERVATIONS),
            "count": len(items), "items": items}


@router.post("/decision-feed/generate", dependencies=[Depends(require_role(Role.ANALYST))])
async def generate_feed(req: GenerateRequest | None = None,
                        session: AsyncSession = Depends(get_session),
                        user: User = Depends(acting_user)) -> dict:
    return await PipelineOrchestrator().generate(session, user, req or GenerateRequest())


@router.get("/decision-feed/latest")
async def latest_feed(session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    res = await session.execute(
        select(DecisionFeed).options(selectinload(DecisionFeed.items))
        .where(DecisionFeed.user_id == user.id)
        .order_by(desc(DecisionFeed.generated_at)).limit(1)
    )
    feed = res.scalar_one_or_none()
    if feed is None:
        return {"feed": None, "message": "No feeds persisted yet - POST /api/v1/decision-feed/generate first."}
    return {
        "feed_id": str(feed.id),
        "generated_at": feed.generated_at.isoformat() if feed.generated_at else None,
        "status": feed.status, "item_count": len(feed.items),
        "items": [{
            "id": str(i.id), "title": i.title, "action_type": i.action_type,
            "impact_score": i.impact_score, "confidence": i.confidence,
            "veto_flag": i.veto_flag, "time_sensitivity": i.time_sensitivity,
            "risk_critique": i.risk_critique,
            "expires_at": (i.payload or {}).get("expires_at"),
            "stale": _is_stale((i.payload or {}).get("expires_at")),
        } for i in feed.items],
    }
