"""Planning / goals + goal projector + mix check."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.auth import Role, require_role
from app.core.database import get_session
from app.engines.allocation_engine import AllocationEngine
from app.engines.simulation_engine import SimulationEngine
from app.models.tables import User
from app.services.allocation_mix import OBJ_TARGET as _OBJ, current_mix
from app.services.intake_service import list_positions
from app.services.plan_service import effective_caps, get_plan, upsert_plan

router = APIRouter(prefix="/api/v1", tags=["plan"])

PERIOD_MULT = {"monthly": 12, "quarterly": 4, "yearly": 1}
OBJ_TARGET = {
    "Grow": {"Equities": 0.80, "Fixed Income": 0.10, "Commodities": 0.10},
    "Balanced": {"Equities": 0.60, "Fixed Income": 0.30, "Commodities": 0.10},
    "Preserve": {"Equities": 0.30, "Fixed Income": 0.60, "Cash": 0.10},
    "Income": {"Equities": 0.40, "Fixed Income": 0.50, "Commodities": 0.10},
}


def _classify(ticker: str, market: str) -> str:
    t = ticker.upper()
    if any(k in t for k in ("BOND", "BND", "AGG", "GOV", "GILT")):
        return "Fixed Income"
    if market == "SPOT" or any(k in t for k in ("GOLD", "OIL", "SILVER")):
        return "Commodities"
    return "Equities"


async def _orm(session, user):
    return await list_positions(session, user)


def _portfolio_stats(rows) -> dict:
    nav = ret = vol = 0.0
    for p in rows:
        val = float(p.quantity) * float(p.current_price or 0)
        nav += val
        m = p.meta or {}
        ret += val * (m.get("expected_return_pct") or 0.0)
        vol += val * (m.get("volatility_pct") or 12.0)
    if not nav:
        return {"nav": 0.0, "expected_roi": None, "volatility": None}
    return {"nav": nav, "expected_roi": round(ret / nav, 2), "volatility": round(vol / nav, 2)}


class PlanRequest(BaseModel):
    objective: str | None = None
    risk_tolerance: str | None = None
    horizon_years: int | None = None
    target_amount: float | None = None
    target_date: str | None = None
    currency: str | None = None
    target_roi_pct: float | None = None
    target_roi_period: str | None = None
    target_yield_pct: float | None = None
    target_yield_period: str | None = None
    preferred_depth: int | None = None


def _plan_dict(plan, stats: dict) -> dict:
    nav = stats["nav"]
    if plan is None:
        base = {"configured": False, "objective": "Balanced", "risk_tolerance": "Medium",
                "horizon_years": 10, "target_amount": None, "target_date": None, "currency": "ILS",
                "target_roi_pct": None, "target_roi_period": "yearly",
                "target_yield_pct": None, "target_yield_period": "yearly", "preferred_depth": None,
                "caps": effective_caps(None), "goal_progress": None}
    else:
        target = float(plan.target_amount) if plan.target_amount is not None else None
        base = {"configured": True, "objective": plan.objective, "risk_tolerance": plan.risk_tolerance,
                "horizon_years": plan.horizon_years, "target_amount": target,
                "target_date": plan.target_date, "currency": plan.currency,
                "target_roi_pct": plan.target_roi_pct, "target_roi_period": plan.target_roi_period or "yearly",
                "target_yield_pct": plan.target_yield_pct, "target_yield_period": plan.target_yield_period or "yearly",
                "preferred_depth": plan.preferred_depth,
                "caps": effective_caps(plan), "current_value": nav,
                "goal_progress": round(min(1.0, nav / target), 4) if target else None}
    base["portfolio_expected_roi_pct"] = stats["expected_roi"]
    roi = base.get("target_roi_pct")
    if roi and stats["expected_roi"] is not None:
        annual_target = roi * PERIOD_MULT.get(base.get("target_roi_period", "yearly"), 1)
        base["roi_annual_target_pct"] = round(annual_target, 2)
        base["roi_on_track"] = stats["expected_roi"] >= annual_target
    return base


@router.get("/plan")
async def get_my_plan(session: AsyncSession = Depends(get_session), user: User = Depends(acting_user)) -> dict:
    plan = await get_plan(session, user)
    return _plan_dict(plan, _portfolio_stats(await _orm(session, user)))


@router.put("/plan", dependencies=[Depends(require_role(Role.ANALYST))])
async def put_my_plan(req: PlanRequest, session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    plan = await upsert_plan(session, user, **req.model_dump())
    await session.commit()
    return _plan_dict(plan, _portfolio_stats(await _orm(session, user)))


@router.get("/plan/projection")
async def goal_projection(session: AsyncSession = Depends(get_session), user: User = Depends(acting_user)) -> dict:
    rows = await _orm(session, user)
    stats = _portfolio_stats(rows)
    plan = await get_plan(session, user)
    if not stats["nav"]:
        return {"message": "Add holdings to project your goal."}
    years = max(1, plan.horizon_years if plan else 10)
    target = float(plan.target_amount) if (plan and plan.target_amount) else None
    sim = SimulationEngine(seed=7).run(
        initial_value=stats["nav"], expected_return_pct=stats["expected_roi"] or 6.0,
        volatility_pct=stats["volatility"] or 12.0, horizon_years=years, target_value=target)
    return {
        "years": years, "starting_value": round(stats["nav"], 2),
        "projected_median": round(sim.nominal.p50, 2),
        "projected_low": round(sim.nominal.p5, 2), "projected_high": round(sim.nominal.p95, 2),
        "target_amount": target,
        "on_track": (sim.nominal.p50 >= target) if target else None,
        "probability_meets_target": (round(sim.probability_meets_target, 3)
                                     if sim.probability_meets_target is not None else None),
        "probability_of_loss_real": round(sim.probability_of_loss_real, 3),
        "probability_of_gain_real": round(sim.probability_of_gain_real, 3),
        "runs": sim.runs,
        "assumptions": [f"~{stats['expected_roi'] or 6}% expected return, {stats['volatility'] or 12}% volatility, over {years} years"],
    }


@router.get("/mix")
async def mix_check(session: AsyncSession = Depends(get_session), user: User = Depends(acting_user)) -> dict:
    rows = await _orm(session, user)
    current, nav = current_mix(rows)
    if not nav:
        return {"message": "Add holdings to check your mix."}
    plan = await get_plan(session, user)
    target = _OBJ.get(plan.objective if plan else "Balanced", _OBJ["Balanced"])
    report = AllocationEngine().compute(target_allocation=target, current_allocation=current, nav=nav)
    return {"note": "Holdings are classified roughly by ticker/venue.",
            "objective": plan.objective if plan else "Balanced", **report.model_dump()}
