"""Market data + research endpoints (Sections AE, AA)."""
from fastapi import APIRouter, HTTPException, Query

from app.agents.research_agent import ResearchAgent
from app.providers.registry import guarded_fx, guarded_quote, provider_health
from app.providers.resilience import CircuitOpenError, RateLimitedError

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
async def research_events() -> dict:
    events = ResearchAgent().scan()
    return {"count": len(events), "events": [e.model_dump() for e in events]}


@router.get("/providers/health")
async def providers_health() -> dict:
    return provider_health()
