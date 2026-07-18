"""Actionable recommendations endpoint (Today view)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.database import get_session
from app.models.tables import User
from app.services.recommendations import (
    apply_recommendation, build_recommendations, complete_recommendation,
    dismiss_recommendation, restore_completed, restore_dismissed,
)

router = APIRouter(prefix="/api/v1", tags=["recommendations"])


@router.get("/recommendations")
async def recommendations(session: AsyncSession = Depends(get_session),
                          user: User = Depends(acting_user)) -> dict:
    return await build_recommendations(session, user)


@router.post("/recommendations/{rec_id}/accept")
async def accept_recommendation(rec_id: str, session: AsyncSession = Depends(get_session),
                                user: User = Depends(acting_user)) -> dict:
    """Apply a recommendation to the portfolio/plan immediately."""
    result = await apply_recommendation(session, user, rec_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Recommendation not found (it may have changed).")
    # Accepting/completing is NOT the same as ignoring: it goes in the completed
    # bucket so the card is gone rather than parked in the ignored list.
    await complete_recommendation(session, user, rec_id)
    return {"ok": True, **result}


@router.post("/recommendations/{rec_id}/dismiss")
async def dismiss_recommendation_route(rec_id: str, session: AsyncSession = Depends(get_session),
                                       user: User = Depends(acting_user)) -> dict:
    """Mark a recommendation as ignored so it stops showing AND stops notifying."""
    await dismiss_recommendation(session, user, rec_id)
    return {"ok": True}


@router.post("/recommendations/restore")
async def restore_recommendations(session: AsyncSession = Depends(get_session),
                                  user: User = Depends(acting_user)) -> dict:
    """Un-ignore every dismissed recommendation so they reappear on Today."""
    return {"ok": True, "restored": await restore_dismissed(session, user)}


@router.post("/recommendations/restore-completed")
async def restore_completed_recommendations(session: AsyncSession = Depends(get_session),
                                            user: User = Depends(acting_user)) -> dict:
    """Bring back cards previously marked as done."""
    return {"ok": True, "restored": await restore_completed(session, user)}
