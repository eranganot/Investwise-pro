"""Learning Loop endpoints (Section 9)."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.auth import Role, require_role
from app.engines.learning_engine import compute_profile
from app.models.tables import DecisionItem, UserAction
from app.services.feed_service import ensure_superadmin

router = APIRouter(prefix="/api/v1", tags=["learning"])


class ActionRequest(BaseModel):
    decision_item_id: UUID
    action: str  # accepted | ignored
    note: str | None = None


@router.post("/actions", dependencies=[Depends(require_role(Role.ANALYST))])
async def record_action(req: ActionRequest, session: AsyncSession = Depends(get_session)) -> dict:
    if req.action not in ("accepted", "ignored"):
        raise HTTPException(400, "action must be 'accepted' or 'ignored'")
    item = (await session.execute(
        select(DecisionItem).where(DecisionItem.id == req.decision_item_id)
    )).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "decision_item not found")
    user = await ensure_superadmin(session)
    session.add(UserAction(
        user_id=user.id, decision_item_id=item.id, action=req.action, note=req.note,
    ))
    await session.commit()
    profile = await compute_profile(session, user.id)
    return {"recorded": req.action, "decision_item_id": str(item.id), "profile": profile}


@router.get("/learning/profile")
async def learning_profile(session: AsyncSession = Depends(get_session)) -> dict:
    user = await ensure_superadmin(session)
    return await compute_profile(session, user.id)
