"""Section X - the five core user workflows."""
import hashlib

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.database import get_session
from app.engines.lag_engine import LagEngine
from app.engines.risk_engine import RiskEngine
from app.engines.scenario_engine import SUPPORTED, ScenarioEngine
from app.engines.state_machine import DisplayedItem, StateMachine
from app.engines.xai_engine import XaiEngine
from app.models.tables import User
from app.services.demo_data import DEFAULT_OBSERVATIONS
from app.services.intake_service import list_positions as orm_list_positions, position_to_observation
from app.services.plan_service import effective_caps, get_plan
from app.services.portfolio_analytics import (
    compute_snapshot, health_opportunities, health_scores, load_positions,
    risk_alerts, tax_opportunities,
)

router = APIRouter(prefix="/api/v1", tags=["workflows"])

CATEGORY = {"Buy": "BUY", "Sell": "SELL", "Rebalance": "REBALANCE",
            "Tax": "TAX_OPTIMIZATION", "Risk": "RISK_REDUCTION"}
FEED_CATEGORIES = ["BUY", "SELL", "REBALANCE", "TAX_OPTIMIZATION", "RISK_REDUCTION", "WATCHLIST"]


# --- X.1 Portfolio Health Check ---
@router.get("/health-check")
async def portfolio_health_check(entity: str | None = None,
                                 session: AsyncSession = Depends(get_session),
                                 user: User = Depends(acting_user)) -> dict:
    snap = compute_snapshot(await load_positions(session, user, entity))
    if not snap["nav"]:
        return {"wealth_health_score": None,
                "message": "No portfolio value. POST /api/v1/intake/portfolio first."}
    cap = effective_caps(await get_plan(session, user))["concentration_cap"]
    sc = health_scores(snap, cap)
    return {
        "wealth_health_score": sc["wealth_health_score"],
        "risk_score": sc["risk_score"],
        "tax_efficiency_score": sc["tax_efficiency_score"],
        "liquidity_score": sc["liquidity_score"],
        "diversification_score": sc["diversification_score"],
        "top_improvement_opportunities": health_opportunities(snap, sc),  # capped at 5
    }


# --- X.3 Tax Optimization Review ---
@router.get("/tax/review")
async def tax_optimization_review(entity: str | None = None,
                                  session: AsyncSession = Depends(get_session),
                                  user: User = Depends(acting_user)) -> dict:
    positions = await load_positions(session, user, entity)
    if not positions:
        return {"opportunity_count": 0, "total_estimated_annual_savings_currency": 0.0,
                "opportunities": [], "message": "No positions."}
    return tax_opportunities(positions)


# --- X.4 Risk Alert Center ---
@router.get("/risk/alerts")
async def risk_alert_center(entity: str | None = None,
                            session: AsyncSession = Depends(get_session),
                            user: User = Depends(acting_user)) -> dict:
    snap = compute_snapshot(await load_positions(session, user, entity))
    cap = effective_caps(await get_plan(session, user))["concentration_cap"]
    return risk_alerts(snap, cap)


# --- X.5 Scenario Planning ---
class ScenarioRequest(BaseModel):
    scenario: str
    nav: float | None = None
    custom_delta_pct: float | None = None
    custom_drawdown: float | None = None
    custom_recovery_days: int | None = None
    allocation: dict[str, float] | None = None


@router.get("/scenario/supported")
async def scenario_supported() -> dict:
    return {"supported": SUPPORTED}


@router.post("/scenario")
async def scenario_planning(req: ScenarioRequest,
                            session: AsyncSession = Depends(get_session),
                            user: User = Depends(acting_user)) -> dict:
    nav = req.nav
    if nav is None:
        nav = compute_snapshot(await load_positions(session, user))["nav"] or 1_000_000.0
    try:
        return ScenarioEngine().run(
            req.scenario, nav, custom_delta_pct=req.custom_delta_pct,
            custom_drawdown=req.custom_drawdown, custom_recovery_days=req.custom_recovery_days,
            allocation=req.allocation)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --- X.2 Weekly Decision Feed (cap 10, categorized) ---
@router.get("/decision-feed/weekly")
async def weekly_decision_feed(session: AsyncSession = Depends(get_session),
                               user: User = Depends(acting_user)) -> dict:
    lag = LagEngine()
    sm = StateMachine(risk=RiskEngine(seed=7))
    positions = await orm_list_positions(session, user)
    observations = [o for p in positions if (o := position_to_observation(p)) is not None] \
        or DEFAULT_OBSERVATIONS
    items = []
    for s in lag.scan(observations):
        result = sm.run(s)
        if isinstance(result, DisplayedItem):
            ranked = result.source
            items.append({
                "recommendation_id": "rec_" + hashlib.sha1(
                    f"{s.ticker}{s.action_type.value}".encode()).hexdigest()[:6],
                "ticker": s.ticker,
                "category": CATEGORY.get(s.action_type.value, "WATCHLIST"),
                "path": result.path,
                "impact_score": round(ranked.impact_score, 1),
                "confidence": round(ranked.confidence, 1),
                "time_sensitivity": "Now" if ranked.urgency >= 70 else "This Week" if ranked.urgency >= 40 else "Monitor",
                "explanation": XaiEngine().build(result).model_dump(),
            })
    items.sort(key=lambda x: x["impact_score"], reverse=True)
    return {"feed_max": 10, "count": min(len(items), 10),
            "supported_categories": FEED_CATEGORIES, "items": items[:10]}  # cap at 10
