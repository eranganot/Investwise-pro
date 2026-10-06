"""Planning / goals + goal projector + mix check."""
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.auth import Role, require_role
from app.core.database import get_session
from app.engines.allocation_engine import AllocationEngine
from app.engines.simulation_engine import SimulationEngine
from app.models.tables import User
from app.services.allocation_mix import current_mix
from app.services.intake_service import list_positions
from app.services import strategy_catalog
from app.services.plan_service import effective_caps, get_plan, upsert_plan

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["plan"])

PERIOD_MULT = {"monthly": 12, "quarterly": 4, "yearly": 1}

# A private copy of OBJ_TARGET and a private `_classify` used to live here. Both
# had zero readers -- grep found none anywhere in the app -- and both were
# already drifting from `allocation_mix`: the local `_classify` had none of the
# COMMODITY_TICKERS or FIXED_INCOME_TICKERS sets, so it classified GLD, IAU and
# DBC as Equities, which is the exact bug PR 4 fixed in the shared one.
#
# A dead fork of a lookup table is not harmless. It is the same failure PR 8
# removed one layer down: a second answer that nothing keeps in step, waiting
# for someone to import it because it is right there in the file.


async def _orm(session, user):
    return await list_positions(session, user)


async def _measured(session, user) -> dict | None:
    """The book's measured forward assumptions, or None if they cannot be built.

    Wrapped because a plan card that cannot render is worse than one rendering
    on the instrument table and saying so. A failure here degrades to exactly
    the pre-PR-16 behaviour, and `assumption_basis` on the payload still reads
    'static', so the screen never claims a measurement it did not get.
    """
    from app.services.measured_assumptions import book_assumptions
    try:
        m = await book_assumptions(session, user)
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.warning("measured assumptions unavailable; using the instrument table",
                       exc_info=False)
        m = None
    # PR 98: ONE return basis. A fresh goal verdict's own figures win, so this
    # screen, the projection and the goal cards quote the same numbers; the
    # measured blend stays alongside under `measured_*` for comparison.
    try:
        from app.services.frontier_service import book_expectation
        fe = await book_expectation(session, user)
    except Exception:  # noqa: BLE001 -- the measured figures are the safe fallback
        fe = None
    if fe:
        m = dict(m or {})
        m["measured_expected_return_pct"] = m.get("expected_return_pct")
        m["measured_volatility_pct"] = m.get("volatility_pct")
        m["expected_return_pct"] = fe["expected_return_pct"]
        m["volatility_pct"] = fe["volatility_pct"]
        m["basis"] = "frontier"
        m["frontier"] = fe
    return m


def _frontier_basis_text(fe: dict | None) -> str:
    """PR 98: the basis sentence when the goal verdict supplies the numbers."""
    fe = fe or {}
    return (f" (your goal model: {fe.get('confidence') or 'blended'} estimate, recent volatility weighted "
            f"{float(fe.get('recent_weight') or 0):.0%}, market {fe.get('regime') or 'unknown'}; "
            f"the same numbers as your goal cards)")


def _portfolio_stats(rows, measured: dict | None = None) -> dict:
    """NAV plus the book's forward return and volatility.

    PR 16 -- `measured` is the payload from
    `measured_assumptions.book_assumptions()`. Passed, its figures REPLACE the
    per-ticker table for return and volatility, because that table prices TQQQ
    and SOXL as buy-and-hold instruments and this book holds them inside rules
    whose purpose is to be out of them some of the time.

    OMITTED, THIS FUNCTION IS EXACTLY WHAT IT WAS. Eight screens read it and a
    silent change to any of them would be the defect, not the fix -- the same
    discipline W3's engine branch used. NAV is never taken from `measured`: it
    is FX-normalised here and must stay the one number every card sizes against
    (investing-discipline S2).
    """
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
    out = {"nav": nav, "expected_roi": round(ret / nav, 2), "volatility": round(vol / nav, 2),
           "assumption_basis": "static"}
    if measured:
        # Each side is replaced only if the measurement actually produced one.
        # A half-measured blend keeps the table's answer for the other half and
        # the payload says which -- never a number whose halves came from
        # different places without a label.
        if measured.get("expected_return_pct") is not None:
            out["expected_roi"] = round(float(measured["expected_return_pct"]), 2)
        if measured.get("volatility_pct") is not None:
            # ONE VOLATILITY, the blend of what the strategies measured over
            # their own windows. PR 55.1 briefly made the headline a
            # sigma/sqrt(f) "in-market" rescaling of this and kept the blend
            # beside it under `volatility_strategy_longrun`; both keys are gone
            # because the rescaling was wrong. It assumed a sleeve sits flat
            # when its rule is off, and these sleeves hold their base instead --
            # measured, the off days run at 22.46% volatility, not zero. See
            # STATUS.md B1. Until something is measured that this endpoint can
            # actually stand behind, it reports the blend and `/portfolio/risk`
            # reports the realised figure, each labelled.
            out["volatility"] = round(float(measured["volatility_pct"]), 2)
        out["assumption_basis"] = measured.get("basis", "static")
        out["measured_pct_of_book"] = measured.get("measured_pct_of_book")
        out["assumptions"] = measured
        # PR 98: when the goal verdict supplies the numbers, say so and keep the
        # measured blend beside them.
        out["expectation"] = measured.get("frontier")
        out["measured_expected_roi"] = measured.get("measured_expected_return_pct")
        # What the screen used to say, kept beside what it says now. A number
        # that moved without the old one visible is a number the reader cannot
        # check.
        out["static_expected_roi"] = round(ret / nav, 2)
        out["static_volatility"] = round(vol / nav, 2)
    return out


_TARGET_PERIODS = {"monthly": 12, "quarterly": 4, "yearly": 1}


async def _goal(session, user, plan, nav) -> dict:
    """PR 74 -- the ONE goal (`goal_service.compute_goal`). Replaces
    `_auto_target`, which derived the target from LIVE NAV, so progress was
    ``NAV / (NAV x (1+roi)^n)`` -- a constant no market move could change."""
    from app.services.goal_service import compute_goal
    return await compute_goal(session, user, plan, nav)


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
    # Percent of NAV held deliberately in cash. Bounded here rather than only
    # in `book_target`, so a 750 typed for 7.5 is a 422 at the boundary instead
    # of a clamp nobody sees. 50 is the same ceiling `cash_floor_pct` enforces.
    cash_target_pct: float | None = Field(default=None, ge=0.0, le=50.0)
    # The same target as a fixed shekel amount (wins over the %, and clears it).
    cash_target_ils: float | None = Field(default=None, ge=0.0, le=10_000_000.0)
    # When the put-cash-to-work card appears (it still invests down to the target).
    cash_redeploy_at_ils: float | None = Field(default=None, ge=0.0, le=10_000_000.0)
    # PR 91, the frontier's two dials (migration 0026). Bounded at the boundary:
    # past full Kelly the model gives less growth for more risk, so 1.0 is the cap.
    kelly_fraction: float | None = Field(default=None, ge=0.25, le=1.0)
    return_confidence: str | None = Field(default=None, pattern="^(shrunk|blended|measured)$")
    # PR 104c / migration 0027: the goal's two limits. Crash-risk limit = the most
    # chance of halving a suggested protection may carry (100 = no limit);
    # on-track = the chance at which the goal counts as on track.
    halve_limit_pct: float | None = Field(default=None, ge=0.1, le=100.0)
    on_track_pct: float | None = Field(default=None, ge=50.0, le=95.0)


def _plan_dict(plan, stats: dict, goal: dict | None = None) -> dict:
    nav = stats["nav"]
    goal = goal or {}
    if plan is None:
        base = {"configured": False, "objective": "Balanced", "risk_tolerance": "Medium",
                "horizon_years": 10, "target_amount": None, "target_date": None, "currency": "ILS",
                "target_roi_pct": None, "target_roi_period": "yearly",
                "target_yield_pct": None, "target_yield_period": "yearly", "preferred_depth": None,
                "cash_target_pct": None, "cash_target_ils": None, "cash_redeploy_at_ils": None,
                "caps": effective_caps(None), "goal_progress": None}
    else:
        target = goal.get("target")
        base = {"configured": True, "objective": plan.objective, "risk_tolerance": plan.risk_tolerance,
                "horizon_years": plan.horizon_years, "target_amount": target,
                "target_date": plan.target_date, "currency": plan.currency,
                "target_roi_pct": plan.target_roi_pct, "target_roi_period": plan.target_roi_period or "yearly",
                "target_yield_pct": plan.target_yield_pct, "target_yield_period": plan.target_yield_period or "yearly",
                "preferred_depth": plan.preferred_depth,
                # None means nobody has been asked, which is not a deliberate 0.
                "cash_target_pct": getattr(plan, "cash_target_pct", None),
                "cash_target_ils": (float(plan.cash_target_ils)
                                    if getattr(plan, "cash_target_ils", None) is not None else None),
                "cash_redeploy_at_ils": (float(plan.cash_redeploy_at_ils)
                                         if getattr(plan, "cash_redeploy_at_ils", None) is not None
                                         else None),
                "caps": effective_caps(plan), "current_value": nav,
                "goal_progress": goal.get("progress")}
    # PR 74. The whole goal, so the screen can show WHY the target is what it
    # is ("₪20,000 put in, compounding 30%/yr to 31 Dec 2031 -> ₪82,7xx") and
    # where you should be today (`on_track_value`) against where you are.
    base["goal"] = goal or None
    base["portfolio_expected_roi_pct"] = stats["expected_roi"]
    # PR 16 -- where that figure came from. The banner reading "~10.87%/yr vs
    # your 30%/yr target" never said it was an instrument-table guess about
    # buy-and-hold TQQQ; a forward number with no stated basis is not checkable.
    base["assumption_basis"] = stats.get("assumption_basis", "static")
    # PR 98: the goal model's basis when it supplies the number, and the
    # measured blend it replaced, so the screen can show both.
    base["expectation"] = stats.get("expectation")
    base["portfolio_expected_roi_pct_measured"] = stats.get("measured_expected_roi")
    base["measured_pct_of_book"] = stats.get("measured_pct_of_book")
    base["portfolio_expected_roi_pct_static"] = stats.get("static_expected_roi")
    base["portfolio_volatility_pct"] = stats.get("volatility")
    # PR 55.1 also added `portfolio_volatility_pct_strategy_longrun` HERE, under
    # a different name from the one it used in `_portfolio_stats`. PR 56 removed
    # the producer and missed this mapper, so the payload went on carrying the
    # retracted number's NAME with a null value -- the same shape as PR 43, a
    # second call site surviving the fix to the first. A key that only ever
    # holds null is worse than an absent one: it reads as "we measure this and
    # it came back empty". Removed. See STATUS.md B1 / PR 56.1.
    base["portfolio_volatility_pct_static"] = stats.get("static_volatility")
    base["assumptions"] = stats.get("assumptions")
    roi = base.get("target_roi_pct")
    if roi and stats["expected_roi"] is not None:
        annual_target = roi * PERIOD_MULT.get(base.get("target_roi_period", "yearly"), 1)
        base["roi_annual_target_pct"] = round(annual_target, 2)
        base["roi_on_track"] = stats["expected_roi"] >= annual_target
    # PR 91: the frontier's dials, effective value and source. `kelly_fraction`
    # / `return_confidence` above-the-fold are the raw stored values (None =
    # default); this block is what the frontier actually uses.
    from app.services.frontier_service import settings_of
    base["kelly_fraction"] = getattr(plan, "kelly_fraction", None) if plan is not None else None
    base["return_confidence"] = getattr(plan, "return_confidence", None) if plan is not None else None
    base["frontier"] = settings_of(plan)
    return base


@router.get("/plan")
async def get_my_plan(session: AsyncSession = Depends(get_session), user: User = Depends(acting_user)) -> dict:
    plan = await get_plan(session, user)
    stats = _portfolio_stats(await _orm(session, user), await _measured(session, user))
    return _plan_dict(plan, stats, await _goal(session, user, plan, stats["nav"]))


@router.put("/plan", dependencies=[Depends(require_role(Role.ANALYST))])
async def put_my_plan(req: PlanRequest, session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    plan = await upsert_plan(session, user, **req.model_dump())
    stats = _portfolio_stats(await _orm(session, user), await _measured(session, user))
    goal = await _goal(session, user, plan, stats["nav"])
    # `plan.target_amount` is a CACHED COPY of the derived goal, written on save,
    # so anything still reading the column reads the same number. A typed
    # amount (no ROI, basis "saved_amount") is the user's and is left alone.
    if goal.get("basis") == "contributions":
        from decimal import Decimal
        plan.target_amount = Decimal(str(goal["target"]))
    await session.commit()
    return _plan_dict(plan, stats, goal)


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

    There is no ``legacy`` block any more. It carried ``plans.strategy`` and
    ``plans.strategy_sleeve_pct`` through the C1-C4 transition so a reader could
    see a strategy that existed only in the columns. Both columns are gone, and
    the last thing they said on the live book was "btm_swing_dip at 15%" while
    the sleeve row said 38.4% -- a second answer that had quietly gone wrong.
    """
    from app.services import sleeve_service as sv

    from app.services import strategies as static_cat

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
    # TARGET AND ACTUAL, SIDE BY SIDE, because the card printed one and meant
    # the other. `allocated_pct` is what the sleeves are SET to; occupancy is
    # what they HOLD. On this book that is 64% against 15%, and nothing in this
    # payload made the difference visible -- so the screen rendered a target in
    # the present tense and called it the book.
    occ = await sv.sleeve_occupancy(session, user)
    _by_id = {o["strategy_id"]: o for o in occ["sleeves"]}
    _sleeves = []
    for d in sv.as_dicts(sleeves):
        o = _by_id.get(d["strategy_id"]) or {}
        _sleeves.append({**d, "held_pct": o.get("held_pct"),
                         "held_ils": o.get("held_ils"),
                         "gap_pct": o.get("gap_pct"),
                         # WHICH LEG, AND WHEN IT WAS DECIDED. Since PR 63
                         # `tickers` follows the rule's stored signal rather than
                         # the catalogue's static weights, so a screen showing
                         # QQQ where the catalogue says TQQQ must be able to say
                         # why. Without these two the payload cannot distinguish
                         # "the rule stepped back on 09-05" from a default nobody
                         # produced -- which is the distinction PR 63 exists to
                         # make possible.
                         "following_signal": o.get("following_signal"),
                         "signal_as_of": o.get("signal_as_of"),
                         "tickers": o.get("tickers")})
    # PR 71: what the unclaimed remainder is split into, from THE book target,
    # so the Plan tab can say the core is not all of the remainder.
    _comm = _cashr = 0.0
    try:
        from app.services.book_target import book_target as _bt
        _p = await get_plan(session, user)
        _btv = await _bt(session, user, getattr(_p, "objective", None) or "Balanced")
        _comm, _cashr = float(_btv.get("commodities_pct") or 0.0), float(_btv.get("cash_pct") or 0.0)
    except Exception:  # noqa: BLE001 -- the sleeve table must still render
        logger.warning("book target unavailable for the sleeves view", exc_info=False)
    return {
        "sleeves": _sleeves,
        "commodities_pct": round(_comm, 4),
        "cash_reserve_pct": round(_cashr, 4),
        "allocated_pct": sv.total_pct(sleeves),
        "allocated_held_pct": occ["allocated_held_pct"],
        "allocated_held_ils": occ["allocated_held_ils"],
        "core_pct": sv.remainder_pct(sleeves),
        # The core's ACTUAL share against its target. Same distinction.
        "core_held_pct": occ["core_held_pct"],
        "nav_ils": occ["nav_ils"],
        # C6. Which strategy manages the core, or None for "the objective does".
        # The core's SIZE is still `core_pct` above and still computed -- this
        # names the manager, it does not claim a share of the book.
        "core": core,
        # Still true, and still worth saying: the core is a remainder. C6 gave it
        # a name, not a percentage of its own.
        "core_is_implicit": True,
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


@router.post("/plan/sleeves/{strategy_id}/swap",
             dependencies=[Depends(require_role(Role.ANALYST))])
async def swap_my_sleeve(strategy_id: str, to: str,
                         session: AsyncSession = Depends(get_session),
                         user: User = Depends(acting_user)) -> dict:
    """Swap this sleeve for another strategy at the SAME size.

    "Retire the overlay, hold the base" needed this. DELETE retires the whole
    sleeve, which drops the ALLOCATION as well as the rule -- on this book that
    is 36% collapsing into the core and the Fixed Income target swinging from
    7.8% to ~22%. Dropping a rule and dropping an allocation were one operation
    and only one of them was ever wanted.

    Done as one call rather than DELETE-then-POST because the order is forced
    (adding first would exceed 100% and be refused) and because the two-step
    leaves a window in which the book is under-allocated and the core oversized.
    Anything reading the book in that window -- a refresh, a card, the 06:15 job
    -- would see an allocation nobody chose.

    Nothing is bought or sold. The sleeve's ticker changes; the shares do not.
    Afterwards the old ticker is a holding no sleeve wants and the new one is
    unfunded, and the existing cards say both.
    """
    from app.services.strategy_service import swap_sleeve

    return await swap_sleeve(session, user, from_id=strategy_id, to_id=to)


@router.get("/plan/core/options")
async def core_options(session: AsyncSession = Depends(get_session),
                       user: User = Depends(acting_user)) -> dict:
    """The strategies that may manage the core, and what choosing each would mean.

    THE CHOICE WAS NEVER PUT IN FRONT OF ANYONE. `core` has been `null` since
    C6 shipped on 2026-08-13 -- not refused, not deferred, simply never asked.
    The only way to set one was to apply a static family card on the Strategies
    tab, where it reads as "add this strategy" and its side effect of naming
    your whole-book target mix is invisible. The Plan tab said "Core - 36%" and
    one line of grey text, which is a statement about SIZE and silent about who
    manages it.

    So this endpoint exists to make the decision legible: every eligible family,
    what mix it would manage the core to, and what that is expected to cost in
    drawdown -- side by side, in one place, instead of fourteen cards.

    ESTIMATED AND MEASURED ARE KEPT APART. `profile.est_max_drawdown_pct` is a
    modelled figure that ships with the catalogue; `measured` comes from the
    backtest store and is a real result over a real window. They are different
    kinds of claim and the payload never merges them -- `drawdown_source` says
    which one a reader is looking at, and `measured` is simply absent when the
    store has nothing.

    That distinction has teeth right now: production could not read the backtest
    store AT ALL until the `benchmark_ticker` column was added to the startup
    DDL, so every "measured" figure in the app was missing and the reason sat
    unread in `backtest_store_error`. `store_error` is echoed here so a screen
    can say "not measured yet" for the right reason.
    """
    from app.services import backtest_service as bt
    from app.services import sleeve_service as sv
    from app.services import strategies as static_cat
    from app.services.strategy_profile import with_profiles

    plan = await get_plan(session, user)
    current = await sv.core_strategy_id(session, user)

    ids = [c["id"] for c in static_cat.CATALOG]
    measured = await bt.get_many(session, ids)

    # `profile` is DERIVED, not stored on the catalogue -- the same helper the
    # Strategies tab uses, so the two screens cannot quote different numbers for
    # the same family.
    out = []
    for c in with_profiles(static_cat.CATALOG):
        sid = c["id"]
        why = sv.validate_core(sid)
        prof = c.get("profile") or {}
        m = (measured.get(sid) or {})
        metrics = (m.get("metrics") or {}) if m.get("ok") else {}
        est_dd = prof.get("est_max_drawdown_pct")
        meas_dd = metrics.get("max_drawdown_pct")
        out.append({
            "strategy_id": sid,
            "name": c.get("name"),
            "goal": c.get("goal"),
            "objective": c.get("objective"),
            "risk_tolerance": c.get("risk_tolerance"),
            "description": c.get("description"),
            "target_allocation": c.get("target_allocation") or {},
            "holdings_count": prof.get("holdings_count"),
            "expected_return_pct": prof.get("expected_return_pct"),
            "volatility_pct": prof.get("volatility_pct"),
            # The two kinds of drawdown claim, never merged.
            "est_max_drawdown_pct": est_dd,
            "measured_max_drawdown_pct": meas_dd,
            "drawdown_source": "measured" if meas_dd is not None else (
                "estimate" if est_dd is not None else None),
            "measured_period": (m.get("period") if meas_dd is not None else None),
            "is_current": sid == current,
            # Every family here is eligible; the field exists so a rule-based
            # strategy arriving in this list later cannot be offered silently.
            "eligible": why is None,
            "ineligible_reason": why,
            "matches_your_objective": c.get("objective") == getattr(plan, "objective", None),
            "matches_your_risk": c.get("risk_tolerance") == getattr(plan, "risk_tolerance", None),
        })

    return {
        "current": current,
        "options": out,
        "objective": getattr(plan, "objective", None),
        "risk_tolerance": getattr(plan, "risk_tolerance", None),
        # None means "the objective manages the core", which is a real answer
        # and not an error state -- but it should be a CHOSEN one.
        "core_is_unset": current is None,
        "store_error": bt.store_unavailable,
    }


class CoreIn(BaseModel):
    strategy_id: str


@router.post("/plan/core", dependencies=[Depends(require_role(Role.ANALYST))])
async def set_my_core(body: CoreIn,
                      session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    """Name the strategy that manages the core, directly.

    `set_core` has existed since C6 and had no route: the only caller was
    `apply_strategy`, so choosing a core meant applying a whole strategy and
    accepting its other effects. A decision the app asks you to make needs a
    door of its own.

    Buys nothing and sells nothing, exactly like DELETE below. It sets the
    target mix the rest of the book is measured against; the rebalance cards
    recompute on the next load and you decide whether to act on them.
    """
    from app.services import sleeve_service as sv

    out = await sv.set_core(session, user, body.strategy_id)
    if out.get("ok"):
        await session.commit()
    return out


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


@router.get("/plan/target/accepted")
async def target_accepted(session: AsyncSession = Depends(get_session),
                          user: User = Depends(acting_user)) -> dict:
    """The target the user last accepted, for the card to seed itself from.

    Read-only. `accepted` is null when no acceptance stands -- either none was
    ever made, or the last one was undone.
    """
    from app.services.target_apply import last_accepted
    return {"ok": True, "accepted": await last_accepted(session, user)}


@router.get("/plan/target/split")
async def target_split(max_drawdown_pct: float,
                       session: AsyncSession = Depends(get_session),
                       user: User = Depends(acting_user)) -> dict:
    """T6a -- size each sleeve on its own axis instead of at the ratio you run.

    READ-ONLY, and NOT run on card load: two simulations per grid point over a
    ~55-point simplex is minutes of work, so this is an explicit button.

    No `excess_pct` parameter, deliberately. T2 asks "what would it take to reach
    X" and needs a target; this asks "what is the best split at all", which has
    no target to miss -- only a ceiling to respect. Accepting one here would
    invite the reader to think the answer was tuned to it.

    Ranked on `backtest_service.OOS_SPLIT`, never on the full sample the winner
    was chosen from, and every ranked row carries its fit/test gap.
    """
    from app.services import target_solver
    if max_drawdown_pct <= 0:
        return {"ok": False, "reason": "NO_CEILING",
                "detail": "a drawdown ceiling above 0% is required - without one "
                          "the answer is always the most leveraged split allowed"}
    return await target_solver.solve_split_for(
        session, user, max_drawdown_pct=float(max_drawdown_pct))


@router.get("/plan/frontier/inputs")
async def frontier_inputs(session: AsyncSession = Depends(get_session),
                          user: User = Depends(acting_user)) -> dict:
    """PR 89 -- what the efficient frontier would be fed, with provenance.

    READ-ONLY and diagnostic: nothing reads this yet (PR 90 does). It exists so
    the inputs can be inspected on the real book -- the ILS covariance, the
    shrinkage, the prior, which sleeves carry a measured view and why the
    others do not -- before any optimiser is allowed to act on them. Fetches
    ~10 years of closes, so it is a button, never a card load.
    """
    from app.services.frontier_inputs import frontier_inputs_for
    return await frontier_inputs_for(session, user)


class PreviewRequest(BaseModel):
    """PR 104b: what the Edit-goal / Edit-rules sheets would change. Nothing is saved."""
    target_roi_pct: float | None = Field(default=None, ge=0.0, le=500.0)
    target_date: str | None = Field(default=None, pattern=r"^\d{4}$")
    monthly_ils: float | None = Field(default=None, ge=0.0, le=10_000_000.0)
    armed: dict[str, bool] | None = None
    guard_pct: float | None = Field(default=None, gt=0.0, le=90.0)


@router.post("/plan/frontier/preview")
async def frontier_preview(req: PreviewRequest, session: AsyncSession = Depends(get_session),
                           user: User = Depends(acting_user)) -> dict:
    """The goal's odds as they WOULD be. Read-only: no verdict is stored."""
    from app.services import frontier_service as fs
    return await fs.preview(session, user, roi_pct=req.target_roi_pct, target_date=req.target_date,
                            monthly=req.monthly_ils, armed=req.armed, guard_pct=req.guard_pct)


@router.get("/plan/frontier")
async def frontier_latest(session: AsyncSession = Depends(get_session),
                          user: User = Depends(acting_user)) -> dict:
    """PR 90 -- the latest stored frontier verdict, with its age. Never computes."""
    from app.services import frontier_service as fs
    row = await fs.latest(session, user)
    if row is None:
        return {"ok": False, "reason": "NO_VERDICT",
                "detail": "no frontier solve on record - POST /plan/frontier/refresh runs one"}
    return {"ok": True, "age_hours": round(fs._age_hours(row) or 0.0, 2),
            "max_age_hours": fs.MAX_AGE_H, "source": row.source, **(row.detail or {})}


@router.post("/plan/frontier/refresh", dependencies=[Depends(require_role(Role.ANALYST))])
async def frontier_refresh(session: AsyncSession = Depends(get_session),
                           user: User = Depends(acting_user)) -> dict:
    """PR 90 -- solve now and store the verdict. Reads the book; changes nothing.

    ~10-20s: fetches ~10y of closes and resamples the solve. The resize itself
    happens only when the Today card it produces is accepted.
    """
    from app.services import frontier_service as fs
    return await fs.compute_for(session, user, source="on_demand")


@router.get("/plan/target/cores")
async def target_cores(max_drawdown_pct: float, top_n: int = 5,
                       session: AsyncSession = Depends(get_session),
                       user: User = Depends(acting_user)) -> dict:
    """T6b -- measure every catalog family as your core, against your ceiling.

    READ-ONLY, and there is NO apply path from here at all. `/plan/target` has
    `/apply` and `/undo`; this deliberately has neither. Swapping a core is a
    whole-book change, and investing-discipline SS5 is explicit that the app
    never recommends products: a ranked list of the user's own catalog with
    measured numbers beside it is a measurement, and one tap from that list to a
    rebuilt book would turn it into advice with a button.

    Not run on card load. One search is two simulations for each of fifteen
    candidates -- measured at ~54s against live data -- so it is an explicit
    button, and the nightly 04:10 job stores a result for the card to read.

    Ranked on the recent window with the ten-year measured beside every row.
    That is Eran's decision of 2026-08-25, taken with the drawdown figures for
    both windows in front of him and against the spec's own recommendation.
    Every row carries both, and any row whose two windows disagree is flagged,
    because the short window understates the 2022 rate shock roughly six-fold
    for the bond-heavy families a ceiling selects for.
    """
    from app.services import solver_store, target_solver
    if max_drawdown_pct <= 0:
        return {"ok": False, "reason": "NO_CEILING",
                "detail": "a drawdown ceiling above 0% is required - without one every "
                          "core is admissible and the ranking answers nothing"}
    out = await target_solver.solve_core_for(
        session, user, max_drawdown_pct=float(max_drawdown_pct),
        top_n=max(1, min(int(top_n), 15)))
    # Stored so the CARD can read a verdict it could never afford to compute.
    # Same write/read split as PR 11, for the same reason.
    if out.get("ok"):
        await solver_store.record_core(session, user, out, source="on_demand")
    return out


@router.get("/plan/target/sleeve-count")
async def target_sleeve_count(max_drawdown_pct: float, max_measures: int = 60,
                              session: AsyncSession = Depends(get_session),
                              user: User = Depends(acting_user)) -> dict:
    """T6b, second half -- how many sleeves earn their place?

    T2 resizes a fixed set and T6a sizes each member of that fixed set. Neither
    can answer "should there be a third one at all", because both take the
    membership as given -- and this book runs two for no measured reason.

    Every set is measured at an EQUAL split of the same total sleeve share. The
    count and the size are separate questions, and mixing them makes neither
    answerable: a three-sleeve set that wins because the search also handed it
    more of the book has not shown that three beats two.

    READ-ONLY, no apply path, same as /plan/target/cores.
    """
    from app.services import target_solver
    if max_drawdown_pct <= 0:
        return {"ok": False, "reason": "NO_CEILING",
                "detail": "a drawdown ceiling above 0% is required - without one every "
                          "addition is admissible and the count only ever grows"}
    return await target_solver.solve_sleeve_count_for(
        session, user, max_drawdown_pct=float(max_drawdown_pct),
        max_measures=max(1, min(int(max_measures), 200)))


@router.get("/plan/walk-forward")
async def walk_forward_sweep(strategy_id: str,
                             session: AsyncSession = Depends(get_session),
                             user: User = Depends(acting_user)) -> dict:
    """W1 -- does fitting this strategy's parameter beat not fitting it?

    NOT "which value wins". That question always has an answer, the answer is
    always the in-sample winner, and reporting it is how a sweep manufactures an
    edge. Every fold measures the value FITTED on the data available at the time
    against the value ALREADY IN THE CATALOG -- the counterfactual that actually
    existed had nobody swept.

    READ-ONLY and there is no apply path. Nothing here writes a fitted parameter
    back into any spec: `backtest_service` already sets the rule that a
    measured-better variant ships OFF until a human has compared the numbers,
    and a route that tuned a live strategy from one history would be exactly the
    overfitting this module exists to detect.

    An explicit button. Six folds x five values x two phases per strategy, each a
    simulation over its window.
    """
    from app.core.offload import offload
    from app.services import backtest_service as bs

    spec = next((s for s in strategy_catalog.backtestable() if s["id"] == strategy_id), None)
    if spec is None:
        return {"ok": False, "reason": "UNKNOWN_STRATEGY",
                "detail": f"{strategy_id!r} is not a backtestable strategy"}
    return await offload(bs.walk_forward_blocking, spec)


# W3. The excess this compares the deposit against. One percentage point a year
# is deliberately GENEROUS as an "admissible" improvement: PR 13's core search
# and T6a's sleeve sizing both spend their whole budget arguing over less than
# this. Stated here so the comparison cannot be tuned after the numbers are seen
# -- the same rule W1's bar follows.
EXCESS_PP = 1.0


@router.get("/plan/projection")
async def goal_projection(contribution_per_year: float | None = None,
                          session: AsyncSession = Depends(get_session),
                          user: User = Depends(acting_user)) -> dict:
    """Where the book could end up -- and, since W3, what your deposits do to it.

    Three simulations, because the claim Phase W actually makes is comparative:

      1. **base** -- no deposits. Byte for byte the projection this endpoint has
         always returned. Every pre-W3 key still describes THIS run.
      2. **with_contributions** -- the same book plus a deposit rate that was
         MEASURED from your ledger, or one you passed explicitly. Absent, with a
         named reason, when neither exists. Never an assumed savings rate.
      3. **excess_comparison** -- the base run again at +1pp of annual return.

    (3) is not decoration. The spec's claim is that *"at the book's current NAV
    the contribution rate moves terminal value more than any admissible excess
    does"*, and a claim like that is either on screen next to its rival or it is
    just a sentence. `moves_more` names the winner and can say "excess" -- if
    deposits stop mattering at some NAV, this is what shows it.

    The card also stops dropping the engine's own caveat. Until W3 `assumptions`
    was rebuilt here from scratch and silently omitted *"no rebalancing or cash
    flows during the horizon"*, so the card showed a no-contribution projection
    without ever saying it was one.
    """
    from app.services.intake_service import contribution_rate

    rows = await _orm(session, user)
    stats = _portfolio_stats(rows, await _measured(session, user))
    plan = await get_plan(session, user)
    if not stats["nav"]:
        return {"message": "Add holdings to project your goal."}
    goal = await _goal(session, user, plan, stats["nav"])
    target = goal.get("target")
    # The deadline governs (Eran, Q1): years LEFT to it, the same figure the
    # behind card uses. Horizon only when there is no deadline at all.
    if goal.get("deadline"):
        if not goal.get("years_left"):
            # A deadline that has passed. Projecting over the horizon instead
            # quoted "6 years" and a 99% chance against a date already gone.
            return {"message": (f"Your goal's deadline ({goal['deadline']}) has passed. "
                                f"Set a new deadline to project it."),
                    "target_amount": target, "deadline": goal["deadline"], "years": 0}
        years = goal["years_left"]
    else:
        years = max(1, plan.horizon_years if plan else 10)
    roi = stats["expected_roi"] or 6.0
    vol = stats["volatility"] or 12.0

    from app.engines.return_convention import CONVENTION, CONVENTION_NOTE, drift_pct

    def _run(rate: float | None = None, **kw):
        # Same seed on every run, so the three are compared on the same draws and
        # a difference between them is the lever, not the sampler.
        # PR 76: `rate` is a COMPOUND rate; the engine gets its drift, so the
        # median grows at the stated rate (see `engines.return_convention`).
        args = {"initial_value": stats["nav"],
                "expected_return_pct": drift_pct(roi if rate is None else rate, vol),
                "volatility_pct": vol, "horizon_years": years, "target_value": target}
        args.update(kw)
        return SimulationEngine(seed=7).run(**args)

    sim = _run()

    # THE RATE. Measured, or explicitly given, or absent with a reason.
    measured = await contribution_rate(session, user)
    # A STANDING ORDER IS A DECLARED RATE, not an assumed one. The rule here is
    # "never an assumed savings rate on someone else's card"; an order the user
    # set up is the opposite of an assumption -- it is the deposit the app will
    # itself record on the day. It outranks a rate measured from past deposits
    # because it is the forward-looking fact; an explicit override still wins.
    from app.services.standing_order_service import monthly_total
    standing_monthly = await monthly_total(session, user)
    rate, source = None, None
    if contribution_per_year is not None:
        rate, source = float(contribution_per_year), "override"
    elif standing_monthly > 0:
        rate, source = round(standing_monthly * 12, 2), "standing_order"
    elif measured.get("ok"):
        rate, source = measured["per_year"], "measured"

    contrib_block: dict = {"available": False, "source": None,
                           "measured": measured,
                           "note": measured.get("detail")}
    if rate is not None and rate > 0:
        c = _run(contribution_per_year=rate)
        contrib_block = {
            "available": True, "source": source, "per_year": round(rate, 2),
            "total_added": c.contribution_total,
            "money_in": c.money_in, "steps": c.steps,
            "timing": c.contribution_timing,
            "projected_median": round(c.nominal.p50, 2),
            "projected_low": round(c.nominal.p5, 2),
            "projected_high": round(c.nominal.p95, 2),
            "uplift_median": round(c.nominal.p50 - sim.nominal.p50, 2),
            "on_track": (c.nominal.p50 >= target) if target else None,
            "probability_meets_target": (round(c.probability_meets_target, 3)
                                         if c.probability_meets_target is not None else None),
            # The one that still means "did I lose money" once deposits exist.
            "probability_of_loss_vs_money_in": (
                round(c.probability_of_loss_vs_money_in, 3)
                if c.probability_of_loss_vs_money_in is not None else None),
            "measured": measured,
            "standing_order_monthly_ils": standing_monthly or None,
            "note": (f"deposits of {rate:,.0f}/yr, {'measured from your ledger' if source == 'measured' else 'as you asked'}, "
                     f"paid at the end of each month. Your ledger also supports "
                     f"{measured.get('per_year_excluding_first')}/yr if the first entry was "
                     f"opening capital rather than a contribution."
                     if source == "measured" else
                     f"your standing order of {standing_monthly:,.0f}/month "
                     f"({rate:,.0f}/yr), paid at the end of each month."
                     if source == "standing_order" else
                     f"deposits of {rate:,.0f}/yr, as you asked, paid at the end of each month."),
        }
    elif rate is not None:
        contrib_block["note"] = (f"a contribution rate of {rate:,.0f}/yr is not a "
                                 f"deposit, so the projection is unchanged.")

    # THE RIVAL LEVER, so the spec's claim is checkable rather than asserted.
    ex = _run(rate=roi + EXCESS_PP)
    excess_uplift = round(ex.nominal.p50 - sim.nominal.p50, 2)
    excess_block = {
        "extra_return_pp": EXCESS_PP,
        "projected_median": round(ex.nominal.p50, 2),
        "uplift_median": excess_uplift,
        "moves_more": None,
        "note": (f"an extra {EXCESS_PP:.1f}pp a year of return is worth "
                 f"{excess_uplift:,.0f} at the median over {years:.1f} years."),
    }
    if contrib_block.get("available"):
        cu = contrib_block["uplift_median"]
        excess_block["moves_more"] = "contributions" if cu > excess_uplift else "excess"
        excess_block["note"] += (
            f" Your deposits are worth {cu:,.0f}. "
            + ("The deposits move terminal value more, which is the whole of Phase W's "
               "claim about a book this size."
               if cu > excess_uplift else
               "The extra return moves terminal value more -- at this NAV and this "
               "deposit rate the spec's claim does not hold, and chasing the excess "
               "is the better-paid lever."))

    return {
        "years": round(years, 1), "starting_value": round(stats["nav"], 2),
        "deadline": goal.get("deadline"),
        "projected_median": round(sim.nominal.p50, 2),
        "projected_low": round(sim.nominal.p5, 2), "projected_high": round(sim.nominal.p95, 2),
        "target_amount": target,
        "on_track": (sim.nominal.p50 >= target) if target else None,
        "probability_meets_target": (round(sim.probability_meets_target, 3)
                                     if sim.probability_meets_target is not None else None),
        # PR 99: the goal's ONE headline chance (with the protective rules as
        # they work, rules-off beside it), so this card quotes the goal cards'
        # figure; the chart's own no-rules run stays labelled as such.
        "goal_headline": ((stats.get("expectation") or {}).get("headline")
                          if stats.get("assumption_basis") == "frontier" else None),
        "probability_of_loss_real": round(sim.probability_of_loss_real, 3),
        "probability_of_gain_real": round(sim.probability_of_gain_real, 3),
        "runs": sim.runs,
        "with_contributions": contrib_block,
        "excess_comparison": excess_block,
        # The engine's OWN assumptions, appended rather than replaced. The
        # cash-flows line is the one this card used to drop.
        # PR 16 -- the basis, on the card. At 45.6% assumed volatility the drag
        # term eats a 10.87% expected return whole, so "which volatility" is not
        # a footnote: it decides whether the median line rises at all.
        "assumption_basis": stats.get("assumption_basis", "static"),
        "measured_pct_of_book": stats.get("measured_pct_of_book"),
        "expected_return_pct_static": stats.get("static_expected_roi"),
        "volatility_pct_static": stats.get("static_volatility"),
        "return_convention": CONVENTION,
        "return_convention_note": CONVENTION_NOTE,
        "assumptions": [f"~{stats['expected_roi'] or 6}%/yr compound return (the median grows at it), "
                        f"{stats['volatility'] or 12}% volatility, over {years:.1f} years"
                        + (_frontier_basis_text(stats.get("expectation"))
                           if stats.get("assumption_basis") == "frontier" else
                           f" ({stats['assumption_basis']}; "
                           f"{stats.get('measured_pct_of_book')}% of the book measured)"
                           if stats.get("assumption_basis") not in (None, "static") else
                           " (instrument table, not measured)"),
                        *sim.assumptions],
    }


@router.get("/mix")
async def mix_check(session: AsyncSession = Depends(get_session), user: User = Depends(acting_user)) -> dict:
    rows = await _orm(session, user)
    current, nav = current_mix(rows)
    if not nav:
        return {"message": "Add holdings to check your mix."}
    plan = await get_plan(session, user)
    # The BOOK's target -- sleeves plus core -- from the one helper Today uses.
    # Sharing a helper was already the rule here; what it shared was a target
    # for 36% of the book, measured by both against all of it. Agreeing on a
    # wrong number is not the property this comment was claiming.
    from app.services.book_target import book_target
    bt = await book_target(session, user, plan.objective if plan else "Balanced")
    report = AllocationEngine().compute(target_allocation=bt["target"],
                                        current_allocation=current, nav=nav)
    out = report.model_dump()
    # Always surface Cash, even at 0%: a missing slice read as "fully invested"
    # when the truth was "cash isn't being tracked".
    if isinstance(out.get("current_allocation"), dict):
        out["current_allocation"].setdefault("Cash", 0.0)
    return {"note": "Holdings are classified roughly by ticker/venue.",
            "objective": plan.objective if plan else "Balanced",
            # The provenance, so a reader can see a 77% Equities target is 64
            # points of sleeve chosen one at a time plus 13 of core.
            "target_from_sleeves": bt["sleeves"], "target_from_core": bt["core"],
            "core_pct_of_book": bt["core_pct"], "target_source": bt["source"],
            "cash_ils": round(current.get("Cash", 0.0) * nav, 2), **out}
