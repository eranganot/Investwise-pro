"""Planning / goals endpoints."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.auth import Role, require_role
from app.core.database import get_session
from app.models.tables import User
from app.services.plan_service import effective_caps, get_plan, upsert_plan
from app.services.portfolio_analytics import compute_snapshot, load_positions

router = APIRouter(prefix="/api/v1", tags=["plan"])


class PlanRequest(BaseModel):
    objective: str | None = None          # Grow | Balanced | Preserve | Income
    risk_tolerance: str | None = None     # Low | Medium | High
    horizon_years: int | None = None
    target_amount: float | None = None
    target_date: str | None = None
    currency: str | None = None


def _plan_dict(plan, nav: float) -> dict:
    if plan is None:
        return {"configured": False, "objective": "Balanced", "risk_tolerance": "Medium",
                "horizon_years": 10, "target_amount": None, "target_date": None,
                "currency": "ILS", "caps": effective_caps(None), "goal_progress": None}
    target = float(plan.target_amount) if plan.target_amount is not None else None
    progress = round(min(1.0, nav / target), 4) if target else None
    return {"configured": True, "objective": plan.objective, "risk_tolerance": plan.risk_tolerance,
            "horizon_years": plan.horizon_years, "target_amount": target,
            "target_date": plan.target_date, "currency": plan.currency,
            "caps": effective_caps(plan), "current_value": nav, "goal_progress": progress}


@router.get("/plan")
async def get_my_plan(session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    plan = await get_plan(session, user)
    nav = compute_snapshot(await load_positions(session, user))["nav"]
    return _plan_dict(plan, nav)


@router.put("/plan", dependencies=[Depends(require_role(Role.ANALYST))])
async def put_my_plan(req: PlanRequest, session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    plan = await upsert_plan(session, user, **req.model_dump())
    await session.commit()
    nav = compute_snapshot(await load_positions(session, user))["nav"]
    return _plan_dict(plan, nav)
