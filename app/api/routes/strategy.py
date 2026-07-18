"""Strategy catalog + apply + load-basket (Plan page)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.auth import Role, require_role
from app.core.database import get_session
from app.models.tables import User
from app.services import strategies as cat
from app.services import strategy_profile as prof
from app.services.allocation_mix import current_mix
from app.services.intake_service import list_positions
from app.services.plan_service import get_plan
from app.services.strategy_service import apply_strategy, load_basket

router = APIRouter(prefix="/api/v1", tags=["strategy"])


@router.get("/strategies")
async def strategies() -> dict:
    # Each strategy carries a computed profile (expected return, vol, drawdown,
    # concentration) so the differences between look-alike baskets are visible.
    by_goal = {g: prof.with_profiles(v) for g, v in cat.by_goal().items()}
    return {"goals": cat.GOAL_ORDER, "by_goal": by_goal}


@router.get("/strategies/{strategy_id}/preview")
async def preview(strategy_id: str, session: AsyncSession = Depends(get_session),
                  user: User = Depends(acting_user)) -> dict:
    """What changes if you apply this: objective, risk, target mix, plus trades."""
    s = cat.get(strategy_id)
    if not s:
        return {"ok": False, "error": "unknown strategy"}
    plan = await get_plan(session, user)
    rows = await list_positions(session, user)
    mix, nav = current_mix(rows)
    result = await apply_strategy_preview(session, user, s, plan, mix, nav)
    return {"ok": True, **result}


async def apply_strategy_preview(session, user, s, plan, mix, nav) -> dict:
    from app.engines.allocation_engine import AllocationEngine
    actions = []
    if nav > 0:
        report = AllocationEngine().compute(target_allocation=s["target_allocation"],
                                            current_allocation=mix, nav=nav)
        actions = [a.model_dump() for a in report.rebalance_actions]
    return {
        "strategy": {**s, "profile": prof.profile(s)},
        "diff": prof.diff_against_plan(s, plan, mix),
        "nav": round(nav, 2),
        "rebalance_actions": actions,
    }


@router.post("/strategies/{strategy_id}/apply", dependencies=[Depends(require_role(Role.ANALYST))])
async def apply(strategy_id: str, session: AsyncSession = Depends(get_session),
                user: User = Depends(acting_user)) -> dict:
    return await apply_strategy(session, user, strategy_id)


class LoadBasketRequest(BaseModel):
    total: float | None = None


@router.post("/strategies/{strategy_id}/load-basket", dependencies=[Depends(require_role(Role.ANALYST))])
async def load(strategy_id: str, req: LoadBasketRequest | None = None,
               session: AsyncSession = Depends(get_session),
               user: User = Depends(acting_user)) -> dict:
    req = req or LoadBasketRequest()
    return await load_basket(session, user, strategy_id, total=req.total)
