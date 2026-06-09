"""Actionable recommendations endpoint (Today view)."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.database import get_session
from app.models.tables import User
from app.services.recommendations import build_recommendations

router = APIRouter(prefix="/api/v1", tags=["recommendations"])


@router.get("/recommendations")
async def recommendations(session: AsyncSession = Depends(get_session),
                          user: User = Depends(acting_user)) -> dict:
    return await build_recommendations(session, user)
