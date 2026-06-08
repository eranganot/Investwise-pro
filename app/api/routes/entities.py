"""Entities + auth-status endpoints (Section 5 multi-entity)."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.config import get_settings
from app.core.database import get_session
from app.models.tables import User
from app.services.intake_service import get_entities

router = APIRouter(prefix="/api/v1", tags=["entities"])


@router.get("/auth/status")
async def auth_status() -> dict:
    return {"auth_enabled": bool(get_settings().require_auth)}


@router.get("/entities")
async def entities(session: AsyncSession = Depends(get_session),
                   user: User = Depends(acting_user)) -> dict:
    return {"user": user.email, "entities": await get_entities(session, user)}
