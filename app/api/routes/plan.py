"""Planning / goals + goal projector + mix check."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.auth import Role, require_role
from app.core.database import get_session
from app.engines.allocation_engine import AllocationEngine
from app.engines.simulation_engine import SimulationEngine
from app.models.tables import User
from app.services.allocation_mix import current_mix
from app.services.intake_service import list_positions
from app.services.plan_service import effective_caps, get_plan, upsert_plan

router = APIRouter(prefix="/api/v1", tags=["plan"])

PERIOD_MULT = {"monthly": 12, "quarterly": 4, "yearly": 1}
OBJ_TARGET = {
    "Grow": {"Equities": 0.80, "Fixed Income": 0.10, "Commodities": 0.10},
    "Balanced": {"Equities": 0.60, "Fixed Income": 0.30, "Commodities": 0.10},
    "Preserve": {"Equities": 0.30, "Fixed Income": 0.60, "Cash": 0.10},
    "Income": {"Equities": 0.40, "Fixed Income": 0.50, "Commodities": 0.10},
}


def _classify(ticker: str, market: str) -> str:
    t = ticker.upper()
    if any(k in t for k in ("BOND", "BND", "AGG", "GOV", "GILT")):
        return "Fixed Income"
    if market == "SPOT" or any(k in t for k in ("GOLD", "OIL", "SILVER")):
        return "Commodities"
    return "Equities"


async def _orm(session, user):
    return await list_positions(session, user)


def _portfolio_stats(rows) -> dict:
    from app.services.fx import price_currency, fx_rate
    from app.services.strategy_profile import assumptions_for
    nav = ret = vol = 0.0
    for p in rows:
        m = p.meta or {}
        rate = fx_rate(price_currency(p.market, m if isinstance(m, dict) else None))
        val = float(p.quantity) * float(p.current_price or 0) * rate  # base-currency value
        nav += val
        # Holdings added through the UI carry no expected_return_pct, and the old
        # `or 0.0` counted them as returning exactly nothing -- so a normal equity
        # book reported "~0%/yr vs your 10% target - behind", which was an
        # artefact of missing metadata, not a fact about the portfolio. Fall back
        # to the instrument's character instead of to zero.
        _ret, _vol = assumptions_for(p.ticker, m.get("asset_class"))
        ret += val * (m.get("expected_return_pct") if m.get("expected_return_pct") is not None else _ret)
        vol += val * (m.get("volatility_pct") if m.get("volatility_pct") is not None else _vol)
    if not nav:
        return {"nav": 0.0, "expected_roi": None, "volatility": None}
    return {"nav": nav, "expected_roi": round(ret / nav, 2), "volatility": round(vol / nav, 2)}


_TARGET_PERIODS = {"monthly": 12, "quarterly": 4, "yearly": 1}


def _auto_target(nav: float, plan) -> float | None:
    """Goal target. In 'auto from ROI' mode (target_roi_pct set) it is derived
    live from the current NAV so it stays aligned with the holdings total and the
    projection: target = NAV x (1 + roi)^periods. Otherwise the saved amount."""
    if plan is None:
        return None
    if plan.target_roi_pct is not None and nav:
        roi = float(plan.target_roi_pct)
        years = max(1, plan.horizon_years or 10)
        n = years * _TARGET_PERIODS.get(plan.target_roi_period or "yearly", 1)
        return round(nav * (1 + roi / 100.0) ** n, 2)
    return float(plan.target_amount) if plan.target_amount is not None else None


class PlanRequest(BaseModel):
    objective: str | None = None
    risk_tolerance: str | None = None
    horizon_years: int | None = None
    target_amount: float | None = None
    target_date: str | None = None
    currency: str | None = None
    target_roi_pct: float | None = None
    target_roi_period: str | None = None
    target_yield_pct: float | None = None
    target_yield_period: str | None = None
    preferred_depth: int | None = None


def _plan_dict(plan, stats: dict) -> dict:
    nav = stats["nav"]
    if plan is None:
        base = {"configured": False, "objective": "Balanced", "risk_tolerance": "Medium",
                "horizon_years": 10, "target_amount": None, "target_date": None, "currency": "ILS",
                "target_roi_pct": None, "target_roi_period": "yearly",
                "target_yield_pct": None, "target_yield_period": "yearly", "preferred_depth": None,
                # Which strategy is applied. The column has existed since
                # 0007_plan_strategy and apply_strategy writes it, but this
                # serializer never returned it -- so nothing outside
                # /strategies/{id}/preview could tell which one was active, and
                # applying one looked like a no-op from every other caller.
                "strategy": None, "strategy_sleeve_pct": None,
                "caps": effective_caps(None), "goal_progress": None}
    else:
        target = _auto_target(nav, plan)
        base = {"configured": True, "objective": plan.objective, "risk_tolerance": plan.risk_tolerance,
                "horizon_years": plan.horizon_years, "target_amount": target,
                "target_date": plan.target_date, "currency": plan.currency,
                "target_roi_pct": plan.target_roi_pct, "target_roi_period": plan.target_roi_period or "yearly",
                "target_yield_pct": plan.target_yield_pct, "target_yield_period": plan.target_yield_period or "yearly",
                "preferred_depth": plan.preferred_depth,
                "strategy": plan.strategy,
                # How much of the book that strategy governs. Serialising the id
                # without the size repeats the bug that hid `strategy` itself:
                # a field the app stores, writes and acts on, that no caller
                # could read back.
                "strategy_sleeve_pct": plan.strategy_sleeve_pct,
                "caps": effective_caps(plan), "current_value": nav,
                "goal_progress": round(min(1.0, nav / target), 4) if target else None}
    base["portfolio_expected_roi_pct"] = stats["expected_roi"]
    roi = base.get("target_roi_pct")
    if roi and stats["expected_roi"] is not None:
        annual_target = roi * PERIOD_MULT.get(base.get("target_roi_period", "yearly"), 1)
        base["roi_annual_target_pct"] = round(annual_target, 2)
        base["roi_on_track"] = stats["expected_roi"] >= annual_target
    return base


@router.get("/plan")
async def get_my_plan(session: AsyncSession = Depends(get_session), user: User = Depends(acting_user)) -> dict:
    plan = await get_plan(session, user)
    return _plan_dict(plan, _portfolio_stats(await _orm(session, user)))


@router.put("/plan", dependencies=[Depends(require_role(Role.ANALYST))])
async def put_my_plan(req: PlanRequest, session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    plan = await upsert_plan(session, user, **req.model_dump())
    await session.commit()
    return _plan_dict(plan, _portfolio_stats(await _orm(session, user)))


@router.get("/plan/sleeves")
async def get_my_sleeves(session: AsyncSession = Depends(get_session),
                         user: User = Depends(acting_user)) -> dict:
    """The strategy sleeves on this book, and the core they leave behind.

    Read-only in every sense: it arms nothing, funds nothing, and writes nothing
    -- not even a self-healing row. ``strategy_service.apply_strategy`` still
    writes the single ``plans.strategy`` pair and is still what the rest of the
    app acts on; this endpoint exists so the sleeve table can be seen and
    verified in production before anything starts depending on it.

    ``core_pct`` is the implicit remainder -- the share of the book the sleeves
    have not claimed, still governed by the objective exactly as today. It is
    computed, not stored, because in this model the core is not a row.

    ``legacy`` carries the old columns unchanged. Until C2 makes ``apply`` write
    a sleeve row, a strategy applied after the one-shot backfill ran will show
    up there and nowhere else, and a reader that could not see that would think
    the book had no strategy at all.
    """
    from app.services import sleeve_service as sv

    from app.services import strategies as static_cat

    plan = await get_plan(session, user)
    sleeves = await sv.list_sleeves(session, user)
    core_row = await sv.get_core(session, user)
    core = None
    if core_row is not None:
        entry = static_cat.get(core_row.strategy_id) or {}
        core = {"strategy_id": core_row.strategy_id,
                "name": entry.get("name") or core_row.strategy_id,
                "objective": entry.get("objective"),
                "risk_tolerance": entry.get("risk_tolerance"),
                "target_allocation": entry.get("target_allocation")}
    return {
        "sleeves": sv.as_dicts(sleeves),
        "allocated_pct": sv.total_pct(sleeves),
        "core_pct": sv.remainder_pct(sleeves),
        # C6. Which strategy manages the core, or None for "the objective does".
        # The core's SIZE is still `core_pct` above and still computed -- this
        # names the manager, it does not claim a share of the book.
        "core": core,
        # Still true, and still worth saying: the core is a remainder. C6 gave it
        # a name, not a percentage of its own.
        "core_is_implicit": True,
        "legacy": {"strategy": getattr(plan, "strategy", None),
                   "strategy_sleeve_pct": getattr(plan, "strategy_sleeve_pct", None)},
    }


@router.post("/plan/sleeves/fund", dependencies=[Depends(require_role(Role.ANALYST))])
async def fund_my_sleeves(dry_run: bool = True,
                          session: AsyncSession = Depends(get_session),
                          user: User = Depends(acting_user)) -> dict:
    """Fund every under-funded sleeve against ONE shared budget.

    Calling the single-sleeve path N times would build N funding plans from the
    same cash and the same trim candidates -- money counted once by the book and
    N times by the app. This plans it once.

    Largest sleeve first when the money will not stretch, each one funded to the
    size you chose or skipped entirely. The response says what would be funded,
    what would not, and the allocation you would end up with against the one you
    asked for, so a partial result cannot read as a success.

    **Preview only in this release.** `dry_run=false` returns the same plan and
    refuses, with the reason. The single-sleeve `load-basket` route still
    executes exactly as it does today.
    """
    from app.services.strategy_service import fund_plan

    return await fund_plan(session, user, dry_run=dry_run)


@router.delete("/plan/sleeves/{strategy_id}",
               dependencies=[Depends(require_role(Role.ANALYST))])
async def remove_my_sleeve(strategy_id: str,
                           session: AsyncSession = Depends(get_session),
                           user: User = Depends(acting_user)) -> dict:
    """Stop running a sleeve, and put its caps back where they belong.

    Removing the row and re-levelling the caps happen together on purpose -- a
    sleeve dropped on its own leaves a live ceiling on a position now held for
    some other reason. The response names every cap it retired, so the change is
    reported rather than discovered later in the Rules screen.

    Nothing is sold. The sleeve stops being a target the app steers toward; the
    shares stay exactly where they are, and what to do with them is yours.
    """
    from app.services.strategy_service import retire_sleeve

    return await retire_sleeve(session, user, strategy_id)


@router.delete("/plan/core", dependencies=[Depends(require_role(Role.ANALYST))])
async def clear_my_core(session: AsyncSession = Depends(get_session),
                        user: User = Depends(acting_user)) -> dict:
    """Go back to a core managed by the objective alone.

    Sells nothing, arms nothing, retires nothing -- unlike removing a sleeve,
    there are no caps behind a core. It drops one target: the book's asset mix
    goes from the family's to the objective's, and the rebalance cards recompute
    against the new one on the next load.
    """
    from app.services import sleeve_service as sv

    out = await sv.clear_core(session, user)
    if out.get("ok"):
        await session.commit()
    return out


@router.get("/plan/target")
async def target_solve(excess_pct: float, max_drawdown_pct: float,
                       session: AsyncSession = Depends(get_session),
                       user: User = Depends(acting_user)) -> dict:
    """What sleeve size beats the benchmark by `excess_pct` within a drawdown ceiling.

    READ-ONLY. Both parameters are required and neither has a default: a target
    return with an implied drawdown tolerance is exactly the half-question this
    endpoint exists to stop being asked. GET, so nothing about it can be mistaken
    for an action.

    Returns one of five outcomes -- REACHED, REACHED_ABOVE_CAP, DRAWDOWN_BOUND,
    UNREACHABLE, NOT_MEASURABLE -- every one of which is a legitimate answer.
    """
    from app.services import target_solver
    if max_drawdown_pct <= 0:
        return {"outcome": target_solver.NOT_MEASURABLE,
                "reason": "NO_CEILING",
                "detail": "a drawdown ceiling above 0% is required — without one "
                          "the answer is always the most leveraged blend allowed",
                "execution_plan": None}
    return await target_solver.solve_for(
        session, user, target_excess_pct=float(excess_pct),
        max_drawdown_pct=float(max_drawdown_pct))


class TargetApplyRequest(BaseModel):
    """Exactly what the card read, echoed back.

    `resizes` is `would_execute.resizes` from the solve, VERBATIM -- including
    `from_pct`. Sending the sizes the recommendation was measured against is what
    lets the server refuse a plan whose premise has since changed, rather than
    silently overwriting the current book with one solved for a different one.
    Re-deriving `from_pct` server-side would throw that check away.
    """
    resizes: list[dict]
    context: dict | None = None


@router.post("/plan/target/apply",
             dependencies=[Depends(require_role(Role.ANALYST))])
async def target_apply(body: TargetApplyRequest, confirm: bool = False,
                       session: AsyncSession = Depends(get_session),
                       user: User = Depends(acting_user)) -> dict:
    """Accept the solver's answer, on the TRACKED BOOK ONLY.

    **No brokerage order is placed. Nothing is bought or sold.** This writes
    `plan_sleeves` -- intended percentages -- and nothing else. That boundary is
    `investing-discipline` section 5, and it is the reason T0-T5 were read-only:
    a return target one tap from a book change is the C5 slider bug with higher
    stakes. Relaxing it costs an explicit `confirm`, a staleness check, an
    all-or-nothing write, and a recorded way back.

    `confirm=true` is required. Refusals return before any row is touched, and
    the session is only committed on success -- so a refusal leaves the book
    byte-identical rather than partly rewritten.
    """
    from app.services.target_apply import apply_target
    out = await apply_target(session, user, resizes=body.resizes,
                             context=body.context, confirm=confirm)
    if out.get("ok"):
        await session.commit()
    else:
        # Explicit. `apply_target` can return ok=False AFTER flushing some steps
        # (a mid-plan refusal), and only a rollback makes "nothing was written"
        # true rather than merely intended.
        await session.rollback()
    return out


@router.post("/plan/target/undo",
             dependencies=[Depends(require_role(Role.ANALYST))])
async def target_undo(confirm: bool = False,
                      session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    """Restore the sizes the last apply replaced. Also places no order."""
    from app.services.target_apply import undo_last
    out = await undo_last(session, user, confirm=confirm)
    if out.get("ok"):
        await session.commit()
    else:
        await session.rollback()
    return out


@router.get("/plan/target/applications")
async def target_applications(limit: int = 20,
                              session: AsyncSession = Depends(get_session),
                              user: User = Depends(acting_user)) -> dict:
    """Every automated change to this book, newest first. Read-only.

    A size on the book with no record of which question produced it is
    unanswerable a month later, which is the state everything before Phase A
    was in.
    """
    from sqlalchemy import select

    from app.models.tables import PlanApplication
    rows = (await session.execute(
        select(PlanApplication).where(PlanApplication.subject == user.email)
        .order_by(PlanApplication.created_at.desc())
        .limit(max(1, min(int(limit), 100))))).scalars().all()
    return {"ok": True, "count": len(rows), "entries": [
        {"id": str(r.id), "action": r.action, "at": r.created_at.isoformat() if r.created_at else None,
         "before": dict(r.before_state or {}), "after": dict(r.after_state or {}),
         "context": dict(r.context or {}),
         "allocated_pct_after": r.allocated_pct_after,
         "apply_version": r.apply_version}
        for r in rows]}


@router.get("/plan/projection")
async def goal_projection(session: AsyncSession = Depends(get_session), user: User = Depends(acting_user)) -> dict:
    rows = await _orm(session, user)
    stats = _portfolio_stats(rows)
    plan = await get_plan(session, user)
    if not stats["nav"]:
        return {"message": "Add holdings to project your goal."}
    years = max(1, plan.horizon_years if plan else 10)
    target = _auto_target(stats["nav"], plan)
    sim = SimulationEngine(seed=7).run(
        initial_value=stats["nav"], expected_return_pct=stats["expected_roi"] or 6.0,
        volatility_pct=stats["volatility"] or 12.0, horizon_years=years, target_value=target)
    return {
        "years": years, "starting_value": round(stats["nav"], 2),
        "projected_median": round(sim.nominal.p50, 2),
        "projected_low": round(sim.nominal.p5, 2), "projected_high": round(sim.nominal.p95, 2),
        "target_amount": target,
        "on_track": (sim.nominal.p50 >= target) if target else None,
        "probability_meets_target": (round(sim.probability_meets_target, 3)
                                     if sim.probability_meets_target is not None else None),
        "probability_of_loss_real": round(sim.probability_of_loss_real, 3),
        "probability_of_gain_real": round(sim.probability_of_gain_real, 3),
        "runs": sim.runs,
        "assumptions": [f"~{stats['expected_roi'] or 6}% expected return, {stats['volatility'] or 12}% volatility, over {years} years"],
    }


@router.get("/mix")
async def mix_check(session: AsyncSession = Depends(get_session), user: User = Depends(acting_user)) -> dict:
    rows = await _orm(session, user)
    current, nav = current_mix(rows)
    if not nav:
        return {"message": "Add holdings to check your mix."}
    plan = await get_plan(session, user)
    # The core's mix if one is chosen, the objective's otherwise -- the same
    # answer the rebalance cards use, from the same helper. Two places computing
    # "what is my target mix" differently is how /mix and Today came to disagree.
    from app.services.recommendations import _core_target
    target = await _core_target(session, user, plan.objective if plan else "Balanced")
    report = AllocationEngine().compute(target_allocation=target, current_allocation=current, nav=nav)
    out = report.model_dump()
    # Always surface Cash, even at 0%: a missing slice read as "fully invested"
    # when the truth was "cash isn't being tracked".
    if isinstance(out.get("current_allocation"), dict):
        out["current_allocation"].setdefault("Cash", 0.0)
    return {"note": "Holdings are classified roughly by ticker/venue.",
            "objective": plan.objective if plan else "Balanced",
            "cash_ils": round(current.get("Cash", 0.0) * nav, 2), **out}
