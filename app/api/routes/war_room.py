"""Agent War Room endpoint.

The war room is the audit trail *for* the Today view, not a parallel universe:
`_war_room_payload` is the single entry point, and the recommendations service
calls it to surface approved signals as Today cards. Previously the two ran
independently, so the agents could approve "Buy TEVA" and Today never mentioned it.
"""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.config import get_settings
from app.core.database import get_session
from app.models.tables import User
from app.services import signal_service
from app.services.demo_data import DEFAULT_OBSERVATIONS
from app.services.intake_service import list_positions
from app.services.plan_service import get_plan, plan_settings
from app.services.war_room import build_war_room

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["war-room"])


async def _war_room_payload(session: AsyncSession, user: User, positions=None) -> dict:
    """Run the agent pipeline over grounded signals and return the transcript.

    Signals come from real price history (spot vs. its own 50-day trend). The old
    path ran the agents over ``demo_data.DEFAULT_OBSERVATIONS`` -- TEVA at a
    made-up spot of 100 against a made-up listing of 108.2 -- and presented the
    verdict as a real recommendation. ``DEMO_SIGNALS=1`` restores that for local
    demos only; when signals aren't grounded, nothing is promoted to Today.
    """
    if positions is None:
        positions = await list_positions(session, user)
    port_tickers = {(p.ticker or "").upper() for p in positions}

    observations, grounded = [], True
    try:
        observations = signal_service.build_observations(
            signal_service.candidate_set(positions))
    except Exception:  # noqa: BLE001
        logger.warning("grounded signal build failed", exc_info=True)
        observations = []
    if not observations and get_settings().demo_signals:
        observations, grounded = list(DEFAULT_OBSERVATIONS), False

    ps = plan_settings(await get_plan(session, user))
    out = build_war_room(observations, portfolio_tickers=port_tickers, settings=ps)
    out["grounded"] = grounded
    out["signal_basis"] = ("Live price history: each name's latest close against its own "
                           "50-day trend. Divergence is measured, not forecast."
                           if grounded else "SAMPLE DATA - not real prices.")
    if not observations:
        out["message"] = ("No tradable signals right now - every candidate is sitting close to "
                          "its own trend, or price history was unavailable.")
    return out


@router.get("/war-room")
async def war_room(session: AsyncSession = Depends(get_session),
                   user: User = Depends(acting_user)) -> dict:
    return await _war_room_payload(session, user)
