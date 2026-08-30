"""Opportunity screener endpoint - fundamentals-ranked buy ideas (Today/Explore).

THE DEFAULT IS NOW THE USER'S OBJECTIVE, not a fixed factor mix.

Before item 2, this route's only tilt input was a query string -- `?value=&
growth=&quality=&income=` -- and nothing in the app ever built that URL. So the
one code path in the entire codebase capable of tilting the screener was
unreachable from the product, and every caller got DEFAULT_WEIGHTS.

Precedence, most specific first:

    1. explicit query params      the caller said exactly what they want
    2. the plan's objective       Grow / Income / Preserve tilt; Balanced does not
    3. DEFAULT_WEIGHTS            no plan, or an objective the table does not know

Step 3 is a real branch, not a fallthrough: `weights_for` returns None for an
objective it has no row for, so a new objective added to the plan and not added
to the table ranks on the default rather than on whichever row looked closest.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.screener_agent import OpportunityAgent
from app.api.deps import acting_user
from app.core.database import get_session
from app.models.tables import User
from app.services import objective_weights as _ow
from app.services.plan_service import get_plan

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
    session: AsyncSession = Depends(get_session),
    user: User = Depends(acting_user),
) -> dict:
    """Rank the candidate universe on fundamentals and return the best buys.

    Ranked for your plan's objective by default. Optional `value`/`growth`/
    `quality`/`income` query params (0-1) override that tilt entirely.
    """
    weights = _weights(value, growth, quality, income)
    source = "query"
    if weights:
        weights = {k: (v if v is not None else 0.0) for k, v in weights.items()}
        objective = None
    else:
        plan = await get_plan(session, user)
        objective = getattr(plan, "objective", None)
        weights = _ow.weights_for(objective)
        source = "objective" if weights is not None else "default"

    ideas = OpportunityAgent().top_ideas(weights=weights, n_equities=n_equities,
                                         n_commodities=n_commodities)
    # The pin travels with the answer. A ranking read later has to be traceable
    # to the tilt that produced it, including whether that tilt was reviewed.
    return {"ok": True, "ranked_for": {**_ow.audit_block(objective), "source": source},
            "tilt_disclosure": _ow.tilt_disclosure(objective), **ideas}
