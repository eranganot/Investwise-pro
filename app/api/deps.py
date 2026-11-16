"""Shared FastAPI dependencies."""
from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, Role, require_role
from app.core.config import get_settings
from app.core.database import get_session
from app.models.tables import User
from app.services.feed_service import ensure_superadmin, ensure_user
from app.services.llm import set_ai_distinct_id


async def acting_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_role(Role.READ_ONLY)),
) -> User:
    """Resolve the acting user from the JWT principal when auth is enforced,
    otherwise the default SuperAdmin (open/demo mode)."""
    if not get_settings().require_auth:
        user = await ensure_superadmin(session)
    else:
        user = await ensure_user(session, principal.sub.lower(),
                                 name=principal.sub, role=principal.role.value)

    # The ASGI middleware opened this request's context. Identifying the stable
    # database UUID here lets all subsequent captures and exceptions inherit it.
    posthog_client = getattr(request.app.state, "posthog_client", None)
    if posthog_client is not None:
        posthog_client.identify_context(str(user.id))
    set_ai_distinct_id(str(user.id))

    # PR 104d: this request honours the user's card thresholds (plans.thresholds).
    try:
        from app.services import thresholds
        from app.services.plan_service import get_plan
        thresholds.use(await get_plan(session, user))
    except Exception:  # noqa: BLE001 -- thresholds never cost a request; defaults apply
        pass
    return user
