"""Shared FastAPI dependencies."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, Role, require_role
from app.core.config import get_settings
from app.core.database import get_session
from app.models.tables import User
from app.services.feed_service import ensure_superadmin, ensure_user


async def acting_user(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_role(Role.READ_ONLY)),
) -> User:
    """Resolve the acting user from the JWT principal when auth is enforced,
    otherwise the default SuperAdmin (open/demo mode)."""
    if not get_settings().require_auth:
        return await ensure_superadmin(session)
    return await ensure_user(session, principal.sub.lower(),
                             name=principal.sub, role=principal.role.value)
