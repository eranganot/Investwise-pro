"""Market data + research endpoints (Sections AE, AA).

Every provider call in this module goes through `offload`. `guarded_quote`,
`guarded_fx` and `guarded_events` are plain `def` functions making SYNCHRONOUS
network calls, and this app runs on one uvicorn worker: called straight from an
`async def` they hold the event loop for the whole round trip, so one slow
upstream is an outage for every other endpoint. Measured in-process with the
provider held at 2.0s, `/health` came back in 2.01s.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.research_agent import ResearchAgent
from app.api.deps import acting_user
from app.core.auth import Role, require_role
from app.core.config import get_settings
from app.core.database import get_session
from app.core.offload import offload
from app.models.tables import User
from app.providers.registry import (economic_provider, guarded_fx, guarded_quote,
                                    provider_health)
from app.providers.resilience import CircuitOpenError, RateLimitedError
from app.services.intake_service import list_positions
from app.services.market_impact import annotate

router = APIRouter(prefix="/api/v1", tags=["market"])


@router.get("/market/quote")
async def market_quote(ticker: str = Query(..., min_length=1)) -> dict:
    try:
        return (await offload(guarded_quote, ticker)).model_dump()
    except RateLimitedError as exc:
        raise HTTPException(429, str(exc))
    except CircuitOpenError as exc:
        raise HTTPException(503, str(exc))


@router.get("/fx/rate")
async def fx_rate(base: str = "USD", quote: str = "ILS") -> dict:
    return (await offload(guarded_fx, base, quote)).model_dump()


@router.get("/research/events")
async def research_events(
    session: AsyncSession = Depends(get_session), user: User = Depends(acting_user)
) -> dict:
    """Read-only market intelligence, annotated with how each event touches the
    user's actual holdings (which positions, how much is exposed, what to do)."""
    events = await offload(ResearchAgent().scan)
    rows = await list_positions(session, user)

    # `last_refreshed` USED TO BE HERE, and it came from `market_status()` --
    # the PRICE refresh clock. The events it sat above were four hardcoded
    # sentences unchanged since the day they were typed, so the card read
    # "Updates hourly - last refreshed 4 minutes ago" over a frozen list. A
    # timestamp borrowed from a different subject is not a small inaccuracy: it
    # is the thing that made the fiction read as reporting. PR 58.
    #
    # `source` / `live` / `source_note` replace it, because the reader's real
    # question is not when this refreshed but whether anything is behind it.
    # `annotate` and the exposure arithmetic are untouched and stay ready for a
    # real provider.
    src = economic_provider().name
    return {"count": len(events), "has_portfolio": bool(rows),
            "source": src, "live": src != "builtin",
            "source_note": ("No market-intelligence source is configured, so "
                            "there is nothing to report here."
                            if src == "builtin" else None),
            "events": annotate(events, rows)}


@router.get("/providers/health", dependencies=[Depends(require_role(Role.SUPERADMIN))])
async def providers_health() -> dict:
    """SUPERADMIN (PR 110, S4): its failing keys name held tickers ("quote:SOXL")."""
    return provider_health()


@router.get("/data-status")
async def data_status(session: AsyncSession = Depends(get_session)) -> dict:
    """Whether the app is on live market data, and which source is actually
    serving prices (the configured provider may fall back to Yahoo)."""
    s = get_settings()
    from app.services.pricing_service import last_source
    effective = await last_source(session) or s.market_data_provider
    live = effective != "builtin"
    return {"live": live, "market_data_provider": s.market_data_provider,
            "effective_source": effective, "fx_provider": s.fx_provider,
            "label": (f"Live market data ({effective})" if live
                      else "Illustrative data - not live market prices")}
