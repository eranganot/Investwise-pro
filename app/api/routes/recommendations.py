"""Actionable recommendations endpoint (Today view)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.database import get_session
from app.models.tables import User
from app.services.recommendations import apply_recommendation, build_recommendations

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
    return {"ok": True, **result}
