"""Opportunity screener endpoint - fundamentals-ranked buy ideas (Today/Explore)."""
from fastapi import APIRouter, Query

from app.agents.screener_agent import OpportunityAgent

router = APIRouter(prefix="/api/v1", tags=["screener"])


def _weights(value: float, growth: float, quality: float, income: float) -> dict | None:
    tilt = {"value": value, "growth": growth, "quality": quality, "income": income}
    # only override when the caller actually changed something
    return tilt if any(v is not None for v in tilt.values()) else None


@router.get("/screener")
async def screener(
    n_equities: int = Query(8, ge=1, le=25),
    n_commodities: int = Query(4, ge=0, le=14),
    value: float | None = Query(None, ge=0, le=1),
    growth: float | None = Query(None, ge=0, le=1),
    quality: float | None = Query(None, ge=0, le=1),
    income: float | None = Query(None, ge=0, le=1),
) -> dict:
    """Rank the candidate universe on fundamentals and return the best buys.

    Optional `value`/`growth`/`quality`/`income` query params (0-1) tilt the
    composite toward the factors you care about most.
    """
    weights = _weights(value, growth, quality, income)
    if weights:
        weights = {k: (v if v is not None else 0.0) for k, v in weights.items()}
    ideas = OpportunityAgent().top_ideas(weights=weights, n_equities=n_equities,
                                         n_commodities=n_commodities)
    return {"ok": True, **ideas}
