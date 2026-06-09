"""Market data + research endpoints (Sections AE, AA)."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.research_agent import ResearchAgent
from app.api.deps import acting_user
from app.core.database import get_session
from app.models.tables import User
from app.providers.registry import guarded_fx, guarded_quote, provider_health
from app.providers.resilience import CircuitOpenError, RateLimitedError
from app.services.intake_service import list_positions
from app.services.market_impact import annotate
from app.services.market_state import status as market_status

router = APIRouter(prefix="/api/v1", tags=["market"])


@router.get("/market/quote")
async def market_quote(ticker: str = Query(..., min_length=1)) -> dict:
    try:
        return guarded_quote(ticker).model_dump()
    except RateLimitedError as exc:
        raise HTTPException(429, str(exc))
    except CircuitOpenError as exc:
        raise HTTPException(503, str(exc))


@router.get("/fx/rate")
async def fx_rate(base: str = "USD", quote: str = "ILS") -> dict:
    return guarded_fx(base, quote).model_dump()


@router.get("/research/events")
async def research_events(
    session: AsyncSession = Depends(get_session), user: User = Depends(acting_user)
) -> dict:
    """Read-only market intelligence, annotated with how each event touches the
    user's actual holdings (which positions, how much is exposed, what to do)."""
    events = ResearchAgent().scan()
    rows = await list_positions(session, user)
    st = market_status()
    return {"count": len(events), "has_portfolio": bool(rows),
            "last_refreshed": st["last_refreshed"],
            "refresh_interval_minutes": st["refresh_interval_minutes"],
            "events": annotate(events, rows)}


@router.get("/providers/health")
async def providers_health() -> dict:
    return provider_health()
