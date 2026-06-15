"""Strategy catalog + apply + load-basket (Plan page)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.auth import Role, require_role
from app.core.database import get_session
from app.models.tables import User
from app.services import strategies as cat
from app.services.strategy_service import apply_strategy, load_basket

router = APIRouter(prefix="/api/v1", tags=["strategy"])


@router.get("/strategies")
async def strategies() -> dict:
    return {"goals": cat.GOAL_ORDER, "by_goal": cat.by_goal()}


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
