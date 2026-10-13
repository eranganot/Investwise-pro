"""Agent War Room endpoint.

The war room is the audit trail *for* the Today view, not a parallel universe:
`services.war_room.war_room_payload` is the single entry point, and the recommendations service
calls it to surface approved signals as Today cards. Previously the two ran
independently, so the agents could approve "Buy TEVA" and Today never mentioned it.
"""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.database import get_session
from app.models.tables import User
from app.services.war_room import war_room_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["war-room"])


# The view narrates (a Gemini call per signal); Today's path passes narrate=False.
_war_room_payload = war_room_payload


@router.get("/war-room")
async def war_room(session: AsyncSession = Depends(get_session),
                   user: User = Depends(acting_user)) -> dict:
    return await war_room_payload(session, user)
