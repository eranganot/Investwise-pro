"""Decision Feed routes - full Lag -> Risk -> Tax -> Decision pipeline,
with the Adversary critique (Section 6), Safety Layer (Section 8), and Learning
Loop personalization (Section 9).

- GET  /decision-feed/demo      : in-memory run (no DB), every item carries an
                                  Adversary critique
- POST /decision-feed/generate  : run + persist; optional portfolio context runs
                                  the Safety Layer (block -> veto); items re-ranked
                                  by the user's learned preferences
- GET  /decision-feed/latest    : read back the most recent persisted feed
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents import adversary
from app.agents.allocation_agent import AllocationAgent, VetoException
from app.engines.allocation_engine import AllocationEngine
from app.core.database import get_session
from app.core.security import require_api_key
from app.engines.lag_engine import LagEngine
from app.engines.learning_engine import compute_profile, impact_boost
from app.engines.risk_engine import RiskEngine
from app.engines.safety_engine import SafetyEngine
from app.engines.state_machine import StateMachine
from app.models.tables import DecisionFeed, DecisionItem
from app.schemas.allocation import AllocationRequest
from app.schemas.lag import LagObservation
from app.schemas.state_machine import ActionType, DisplayedItem, Market, VetoedSignal
from app.services.feed_service import build_recommendation, ensure_superadmin
from app.services.intake_service import list_positions, position_to_observation

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
                          "reason": result.reason,
                          "adversary_critique": result.reason,
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
                          "adversary_critique": crit})
    return {"generated": "demo (Lag-driven pipeline)",
            "backbone_vs_hype": lag.backbone_vs_hype(DEFAULT_OBSERVATIONS),
            "count": len(items), "items": items}


class PortfolioContext(BaseModel):
    holdings: dict[str, float] = {}
    liquidity_ratio: float = 1.0


class GenerateRequest(BaseModel):
    observations: list[LagObservation] | None = None
    portfolio: PortfolioContext | None = None
    from_portfolio: bool = False
    entity_name: str | None = None
    allocation: AllocationRequest | None = None
    asset_class_map: dict[str, str] | None = None


@router.post("/decision-feed/generate", dependencies=[Depends(require_api_key)])
async def generate_feed(
    req: GenerateRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    lag, sm = _machine()
    safety_engine = SafetyEngine()

    user = await ensure_superadmin(session)
    if req and req.from_portfolio:
        positions = await list_positions(session, user, req.entity_name)
        observations = [o for p in positions if (o := position_to_observation(p)) is not None]
        if not observations:
            observations = DEFAULT_OBSERVATIONS
    else:
        observations = (req.observations if req and req.observations else DEFAULT_OBSERVATIONS)
    profile = await compute_profile(session, user.id)   # learning loop
    feed = DecisionFeed(user_id=user.id, horizon="month", status="OPEN")
    session.add(feed)
    await session.flush()

    alloc_report = None
    alloc_agent = None
    if req and req.allocation:
        alloc_report = AllocationEngine().compute(
            target_allocation=req.allocation.target_allocation,
            current_allocation=req.allocation.current_allocation,
            nav=req.allocation.nav,
        )
        alloc_agent = AllocationAgent()

    out = []
    for s in lag.scan(observations):
        result = sm.run(s)

        if isinstance(result, DisplayedItem):
            ranked = result.source
            vetted = ranked.source.source

            if alloc_report is not None and req.asset_class_map and s.action_type == ActionType.BUY:
                ac = req.asset_class_map.get(s.ticker)
                if ac:
                    try:
                        alloc_agent.review_buy(ac, alloc_report)
                    except VetoException as ve:
                        session.add(DecisionItem(
                            feed_id=feed.id, title=result.title, action_type=s.action_type.value,
                            trigger=s.trigger, impact_score=0.0, confidence=0.0, veto_flag=True,
                            time_sensitivity="Monitor", risk_critique=ve.reason,
                            payload={"decision": "VETOED", "reason": ve.reason},
                        ))
                        out.append({"ticker": s.ticker, "decision": "VETOED", "reason": ve.reason,
                                    "adversary_critique": ve.reason, "personalized_impact": -1})
                        continue

            safety = None
            if req and req.portfolio:
                safety = safety_engine.check(
                    holdings=req.portfolio.holdings,
                    liquidity_ratio=req.portfolio.liquidity_ratio,
                    proposals=[{"ticker": s.ticker, "action": s.action_type.value,
                                "weight_delta": 0.05, "risk_score": ranked.scores.risk}],
                )

            if adversary.should_veto(safety):
                reason = "VETO (safety): " + "; ".join(f.detail for f in safety.flags)
                session.add(DecisionItem(
                    feed_id=feed.id, title=result.title, action_type=s.action_type.value,
                    trigger=s.trigger, impact_score=0.0, confidence=0.0, veto_flag=True,
                    time_sensitivity="Monitor", risk_critique=reason,
                    payload={"decision": "VETOED", "reason": reason,
                             "safety_flags": [f.model_dump() for f in safety.flags]},
                ))
                out.append({"ticker": s.ticker, "decision": "VETOED", "reason": reason,
                            "adversary_critique": reason, "personalized_impact": -1,
                            "safety_flags": [f.model_dump() for f in safety.flags]})
                continue

            crit = adversary.critique(path=result.path, risk_critique=vetted.risk_critique,
                                      confidence=ranked.confidence, impact=ranked.impact_score,
                                      safety=safety)
            boost = impact_boost(profile, s.action_type.value)
            personalized = round(ranked.impact_score * boost, 2)
            rec = build_recommendation(result)
            payload = rec.model_dump()
            payload["adversary_critique"] = crit
            if safety:
                payload["safety_flags"] = [f.model_dump() for f in safety.flags]
            session.add(DecisionItem(
                feed_id=feed.id, title=rec.title, action_type=rec.action_type,
                trigger=rec.trigger, execution_plan=rec.execution_plan,
                impact_score=rec.impact_score, confidence=rec.confidence,
                urgency=rec.urgency, complexity=rec.complexity,
                time_sensitivity=rec.time_sensitivity, veto_flag=False,
                risk_critique=crit, payload=payload,
            ))
            out.append({"ticker": s.ticker, "title": rec.title, "path": result.path,
                        "impact_score": rec.impact_score, "personalized_impact": personalized,
                        "time_sensitivity": rec.time_sensitivity, "adversary_critique": crit,
                        "safety_flags": [f.model_dump() for f in safety.flags] if safety else []})

        elif isinstance(result, VetoedSignal):
            session.add(DecisionItem(
                feed_id=feed.id, title=f"{s.action_type.value} {s.ticker}",
                action_type=s.action_type.value, trigger=s.trigger,
                impact_score=0.0, confidence=0.0, veto_flag=True,
                time_sensitivity="Monitor", risk_critique=result.reason,
                payload={"decision": "VETOED", "reason": result.reason},
            ))
            out.append({"ticker": s.ticker, "decision": "VETOED", "reason": result.reason,
                        "adversary_critique": result.reason, "personalized_impact": -1})

    await session.commit()
    out.sort(key=lambda x: x.get("personalized_impact", -1), reverse=True)
    return {
        "feed_id": str(feed.id), "user": user.email,
        "entity": (req.entity_name if req else None),
        "persisted_items": len(out),
        "personalization": {"applied": (profile.get("total_actions") or 0) >= 3, "profile": profile},
        "allocation": alloc_report.model_dump() if alloc_report else None,
        "items": out,
    }


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
        "status": feed.status, "item_count": len(feed.items),
        "items": [{
            "id": str(i.id), "title": i.title, "action_type": i.action_type,
            "impact_score": i.impact_score, "confidence": i.confidence,
            "veto_flag": i.veto_flag, "time_sensitivity": i.time_sensitivity,
            "risk_critique": i.risk_critique,
        } for i in feed.items],
    }
