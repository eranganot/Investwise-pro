"""Agent War Room endpoint."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.database import get_session
from app.models.tables import User
from app.services.demo_data import DEFAULT_OBSERVATIONS
from app.services.intake_service import list_positions, position_to_observation
from app.services.war_room import build_war_room

router = APIRouter(prefix="/api/v1", tags=["war-room"])


@router.get("/war-room")
async def war_room(session: AsyncSession = Depends(get_session),
                   user: User = Depends(acting_user)) -> dict:
    positions = await list_positions(session, user)
    port_obs = [o for p in positions if (o := position_to_observation(p)) is not None]
    port_tickers = {o.ticker for o in port_obs}
    # always include market ideas (incl. ones the agents reject), de-duped against holdings
    market_obs = [o for o in DEFAULT_OBSERVATIONS if o.ticker not in port_tickers]
    return build_war_room(port_obs + market_obs, portfolio_tickers=port_tickers)
