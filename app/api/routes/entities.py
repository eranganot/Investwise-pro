"""Entities + auth-status endpoints (Section 5 multi-entity)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.services.feed_service import ensure_superadmin
from app.services.intake_service import get_entities

router = APIRouter(prefix="/api/v1", tags=["entities"])


@router.get("/auth/status")
async def auth_status() -> dict:
    return {"auth_enabled": bool(get_settings().api_key)}


@router.get("/entities")
async def entities(session: AsyncSession = Depends(get_session)) -> dict:
    user = await ensure_superadmin(session)
    return {"user": user.email, "entities": await get_entities(session, user)}
