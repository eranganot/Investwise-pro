"""Planning / goals + goal projector + mix check."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.auth import Role, require_role
from app.core.database import get_session
from app.models.tables import User
from app.services import strategy_catalog
from app.services import plan_view as pv
from app.services.plan_service import get_plan, upsert_plan

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["plan"])


# A private copy of OBJ_TARGET and a private `_classify` used to live here. Both
# had zero readers -- grep found none anywhere in the app -- and both were
# already drifting from `allocation_mix`: the local `_classify` had none of the
# COMMODITY_TICKERS or FIXED_INCOME_TICKERS sets, so it classified GLD, IAU and
# DBC as Equities, which is the exact bug PR 4 fixed in the shared one.
#
# A dead fork of a lookup table is not harmless. It is the same failure PR 8
# removed one layer down: a second answer that nothing keeps in step, waiting
# for someone to import it because it is right there in the file.


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
    # PR 104d / migration 0028: card-threshold OVERRIDES, merged into what is
    # saved; a null value resets that one to its default.
    thresholds: dict | None = None


async def _goal_chance(session, user) -> dict | None:
    """PR 131 (D96): the goal's one chance, current or labelled old (`frontier_service.goal_chance`)."""
    from app.services import frontier_service as fs
    try:
        return await fs.goal_chance(session, user)
    except Exception:  # noqa: BLE001 -- the plan screen stands without it
        return None


@router.get("/plan")
async def get_my_plan(session: AsyncSession = Depends(get_session), user: User = Depends(acting_user)) -> dict:
    plan = await get_plan(session, user)
    stats = pv.portfolio_stats(await pv.positions(session, user), await pv.book_measured(session, user))
    out = pv.plan_dict(plan, stats, await pv.goal_of(session, user, plan, stats["nav"]))
    out["goal_chance"] = await _goal_chance(session, user)
    return out


@router.put("/plan", dependencies=[Depends(require_role(Role.ANALYST))])
async def put_my_plan(req: PlanRequest, session: AsyncSession = Depends(get_session),
                      user: User = Depends(acting_user)) -> dict:
    fields = req.model_dump()
    th_req = fields.pop("thresholds", None)
    plan = await upsert_plan(session, user, **fields)
    if th_req is not None:
        from app.services import thresholds as _thr
        merged = {**(plan.thresholds or {}), **th_req}
        merged = {k: v for k, v in merged.items() if v is not None}
        try:
            plan.thresholds = _thr.clean(merged) or None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    stats = pv.portfolio_stats(await pv.positions(session, user), await pv.book_measured(session, user))
    goal = await pv.goal_of(session, user, plan, stats["nav"])
    # `plan.target_amount` is a CACHED COPY of the derived goal, written on save,
    # so anything still reading the column reads the same number. A typed
    # amount (no ROI, basis "saved_amount") is the user's and is left alone.
    if goal.get("basis") == "contributions":
        from decimal import Decimal
        plan.target_amount = Decimal(str(goal["target"]))
    await session.commit()
    out = pv.plan_dict(plan, stats, goal)
    out["goal_chance"] = await _goal_chance(session, user)
    return out


@router.get("/plan/sleeves")
async def get_my_sleeves(session: AsyncSession = Depends(get_session),
                         user: User = Depends(acting_user)) -> dict:
    """The strategy sleeves on this book, and the core they leave behind (`plan_view.sleeves_view`)."""
    return await pv.sleeves_view(session, user)


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
    """The strategies that may manage the core, and what choosing each would mean (`plan_view`)."""
    return await pv.core_options_view(session, user)


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


class RuleImpactRequest(BaseModel):
    """PR 124: the rules a card proposes (its `apply.rules`)."""
    rules: list[dict] = Field(min_length=1, max_length=12)


@router.post("/plan/frontier/rule-impact")
async def frontier_rule_impact(req: RuleImpactRequest, session: AsyncSession = Depends(get_session),
                               user: User = Depends(acting_user)) -> dict:
    """What arming these rules would do to the goal's chance. Read-only: nothing is saved."""
    from app.services import frontier_service as fs
    return await fs.preview(session, user, extra_rules=req.rules)


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


@router.get("/plan/optimizer")
async def optimizer_latest(session: AsyncSession = Depends(get_session),
                           user: User = Depends(acting_user)) -> dict:
    """PR 136a: the best book the app can measure for your goal (nightly at 05:10). Never computes."""
    from app.services import book_optimizer
    got = await book_optimizer.load(session, user)
    return got or {"ok": False, "reason": "NOT_RUN",
                   "detail": "the optimiser has not run yet - POST /plan/optimizer/refresh runs it"}


@router.get("/plan/protection")
async def protection_latest(session: AsyncSession = Depends(get_session),
                            user: User = Depends(acting_user)) -> dict:
    """A3r: the protection manager's stored plan (one exit per holding, measured)."""
    from app.services import protection_manager
    got = await protection_manager.load(session, user)
    return got or {"ok": False, "reason": "NOT_RUN",
                   "detail": "the protection manager has not run yet - POST /plan/protection/refresh runs it"}


@router.post("/plan/protection/refresh", dependencies=[Depends(require_role(Role.ANALYST))])
async def protection_refresh(session: AsyncSession = Depends(get_session),
                             user: User = Depends(acting_user)) -> dict:
    """A3r: measure the managed exits on the goal replay now and store the plan. Changes no rule."""
    from app.services import protection_manager
    return await protection_manager.refresh(session, user)


@router.post("/plan/optimizer/refresh", dependencies=[Depends(require_role(Role.ANALYST))])
async def optimizer_refresh(session: AsyncSession = Depends(get_session),
                            user: User = Depends(acting_user)) -> dict:
    """PR 136a: search for the best book now and store it. Slow (dozens of light goal
    solves and one full one). Stores the result only; changes nothing in the book."""
    from app.services import book_optimizer
    return await book_optimizer.refresh(session, user)


@router.get("/plan/sleeve-moves")
async def sleeve_moves_latest(session: AsyncSession = Depends(get_session),
                              user: User = Depends(acting_user)) -> dict:
    """PR 135a: the last scored sleeve moves (nightly at 04:40). Never computes."""
    from app.services import sleeve_moves
    got = await sleeve_moves.load(session, user)
    return got or {"ok": False, "reason": "NOT_SCORED",
                   "detail": "no sleeve moves scored yet - POST /plan/sleeve-moves/refresh runs one"}


@router.post("/plan/sleeve-moves/refresh", dependencies=[Depends(require_role(Role.ANALYST))])
async def sleeve_moves_refresh(session: AsyncSession = Depends(get_session),
                               user: User = Depends(acting_user)) -> dict:
    """PR 135a: score every sleeve move now and store it. Slow (a light goal solve per
    move). Stores scores only; changes nothing in the book."""
    from app.services import sleeve_moves
    return await sleeve_moves.refresh(session, user)


@router.post("/plan/frontier/refresh", dependencies=[Depends(require_role(Role.ANALYST))])
async def frontier_refresh(only_if_stale: bool = False,
                           session: AsyncSession = Depends(get_session),
                           user: User = Depends(acting_user)) -> dict:
    """PR 90 -- solve now and store the verdict. Reads the book; changes nothing.

    ~10-20s: fetches ~10y of closes and resamples the solve. The resize itself
    happens only when the Today card it produces is accepted.

    PR 131: `only_if_stale` is the screens' automatic re-solve. It does nothing
    when the goal's chance is current, or when a solve ran in the last
    `AUTO_RESOLVE_MIN_AGE_H` (two tabs open must not cost two solves).
    """
    from app.services import frontier_service as fs
    if only_if_stale:
        gc = await fs.goal_chance(session, user)
        if gc is not None and gc.get("current"):
            return {"skipped": "CURRENT"}
        row = await fs.latest(session, user)
        age = fs._age_hours(row) if row is not None else None
        if age is not None and age < fs.AUTO_RESOLVE_MIN_AGE_H:
            return {"skipped": "RECENT_SOLVE", "age_hours": round(age, 2)}
    return await fs.compute_for(session, user, source="auto_stale" if only_if_stale else "on_demand")


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


@router.get("/plan/projection")
async def goal_projection(contribution_per_year: float | None = None,
                          session: AsyncSession = Depends(get_session),
                          user: User = Depends(acting_user)) -> dict:
    """Where the book could end up, and what deposits do to it (`projection_service.project`)."""
    from app.services.projection_service import project
    return await project(session, user, contribution_per_year)


@router.get("/mix")
async def mix_check(session: AsyncSession = Depends(get_session), user: User = Depends(acting_user)) -> dict:
    from app.services.book_target import mix_report
    return await mix_report(session, user)
