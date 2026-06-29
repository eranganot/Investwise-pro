"""Markets (futures) + Gemini AI summaries & research endpoints."""
import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.database import get_session
from app.models.tables import User
from app.services import ai_service
from app.services.markets_service import futures_snapshot

router = APIRouter(prefix="/api/v1", tags=["markets-ai"])


@router.get("/markets/futures")
async def markets_futures() -> dict:
    """Key index/commodity/rate/vol futures + derived market regime."""
    return await asyncio.to_thread(futures_snapshot)


@router.get("/markets/summary")
async def markets_summary() -> dict:
    """AI 'what's moving' note over the futures snapshot."""
    return await ai_service.macro_summary()


@router.get("/ai/portfolio-summary")
async def ai_portfolio_summary(session: AsyncSession = Depends(get_session),
                               user: User = Depends(acting_user)) -> dict:
    return await ai_service.portfolio_summary(session, user)


@router.get("/ai/holding/{ticker}/summary")
async def ai_holding_summary(ticker: str, session: AsyncSession = Depends(get_session),
                             user: User = Depends(acting_user)) -> dict:
    return await ai_service.holding_summary(session, user, ticker)


@router.post("/ai/holding/{ticker}/research")
async def ai_holding_research(ticker: str, session: AsyncSession = Depends(get_session),
                              user: User = Depends(acting_user)) -> dict:
    return await ai_service.deep_research(session, user, ticker)
