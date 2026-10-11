"""Section X - the five core user workflows."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.database import get_session
from app.engines.scenario_engine import SUPPORTED, ScenarioEngine
from app.models.tables import User
from app.services.plan_service import effective_caps, get_plan
from app.services.portfolio_analytics import (
    compute_snapshot, health_opportunities, health_scores, load_positions,
    risk_alerts,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["workflows"])

# --- X.1 Portfolio Health Check ---
@router.get("/health-check")
async def portfolio_health_check(entity: str | None = None,
                                 session: AsyncSession = Depends(get_session),
                                 user: User = Depends(acting_user)) -> dict:
    # PR 16. Risk is scored on the book's MEASURED volatility wherever its
    # sleeves have been backtested, not on the per-ticker table -- which priced
    # this book's trend-filtered sleeves as buy-and-hold leveraged funds and so
    # produced a Risk of 15 against a 25% cap. With nothing measured this is
    # None and the snapshot is byte-for-byte what it was.
    from app.services.measured_assumptions import book_assumptions
    try:
        _asm = await book_assumptions(session, user)
    except Exception:  # noqa: BLE001 -- the health card must still render
        logger.warning("measured assumptions unavailable; scoring on the table",
                       exc_info=False)
        _asm = None
    snap = compute_snapshot(await load_positions(session, user, entity),
                            (_asm or {}).get("volatility_pct"))
    if not snap["nav"]:
        return {"wealth_health_score": None,
                "message": "No portfolio value. POST /api/v1/intake/portfolio first."}
    caps = effective_caps(await get_plan(session, user))
    cap = caps["concentration_cap"]
    # Risk is scored against the plan's own volatility budget, so a Grow investor
    # isn't marked down for the volatility their objective requires.
    sc = health_scores(snap, cap, caps.get("volatility_cap"))
    # PR 70. THE SCORE IS "ON PLAN" + "ON TRACK" (Eran, 2026-09-22). The four
    # legacy components below are still computed and returned -- the opportunity
    # list and the AI context read them -- but none of them is the headline any
    # more. `wealth_health_score` carries the new score so every reader of the
    # old key shows the same number as the screen.
    from app.services.plan_health import plan_health
    try:
        ph = await plan_health(session, user)
    except Exception:  # noqa: BLE001 -- fall back to the legacy score, and say so
        logger.warning("plan health unavailable; showing the legacy score", exc_info=True)
        ph = None
    return {
        "wealth_health_score": (ph["score"] if ph else sc["wealth_health_score"]),
        "score_basis": ("on_plan_on_track" if ph else "legacy_components"),
        "label": (ph["label"] if ph else None),
        "on_plan": (ph["on_plan"] if ph else None),
        "on_track": (ph["on_track"] if ph else None),
        "on_track_detail": (ph["on_track_detail"] if ph else None),
        "on_track_basis": (ph["on_track_basis"] if ph else None),
        "plan_parts": (ph["parts"] if ph else None),
        "plan_weights": (ph["weights"] if ph else None),
        "legacy_score": sc["wealth_health_score"],
        "risk_score": sc["risk_score"],
        "tax_efficiency_score": sc["tax_efficiency_score"],
        "liquidity_score": sc["liquidity_score"],
        "diversification_score": sc["diversification_score"],
        # Surfaced so the UI can show how the number is built and, crucially,
        # that nothing invisible is holding it down.
        "weights": sc["weights"],
        "max_achievable": sc["max_achievable"],
        "volatility_cap_pct": round((sc["_vol_cap"] or 0) * 100, 1),
        "avg_volatility_pct": round(snap["avg_volatility_pct"], 1),
        # PR 16 -- where the volatility that produced the Risk score came from,
        # and what the score would have read on the old basis. A number that
        # moved with the old one nowhere on screen is one nobody can check.
        "volatility_basis": snap.get("volatility_basis", "static"),
        "avg_volatility_pct_static": round(snap.get("avg_volatility_pct_static", 0.0), 1),
        "measured_pct_of_book": (_asm or {}).get("measured_pct_of_book"),
        "assumptions": _asm,
        "top_improvement_opportunities": health_opportunities(snap, sc),  # capped at 5
    }


# --- X.4 Risk Alert Center ---
@router.get("/risk/alerts")
async def risk_alert_center(entity: str | None = None,
                            session: AsyncSession = Depends(get_session),
                            user: User = Depends(acting_user)) -> dict:
    snap = compute_snapshot(await load_positions(session, user, entity))
    cap = effective_caps(await get_plan(session, user))["concentration_cap"]
    return risk_alerts(snap, cap)


# --- X.5 Scenario Planning ---
class ScenarioRequest(BaseModel):
    scenario: str
    nav: float | None = None
    custom_delta_pct: float | None = None
    custom_drawdown: float | None = None
    custom_recovery_days: int | None = None
    allocation: dict[str, float] | None = None


@router.get("/scenario/supported")
async def scenario_supported() -> dict:
    return {"supported": SUPPORTED}


@router.post("/scenario")
async def scenario_planning(req: ScenarioRequest,
                            session: AsyncSession = Depends(get_session),
                            user: User = Depends(acting_user)) -> dict:
    nav = req.nav
    if nav is None:
        nav = compute_snapshot(await load_positions(session, user))["nav"] or 1_000_000.0
    try:
        return ScenarioEngine().run(
            req.scenario, nav, custom_delta_pct=req.custom_delta_pct,
            custom_drawdown=req.custom_drawdown, custom_recovery_days=req.custom_recovery_days,
            allocation=req.allocation)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
