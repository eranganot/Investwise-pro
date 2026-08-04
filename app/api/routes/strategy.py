"""Strategy catalog + apply + load-basket (Plan page)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import acting_user
from app.core.auth import Role, require_role
from app.core.database import get_session
from app.models.tables import User
from app.services import backtest_service
from app.services import strategies as cat
from app.services import strategy_catalog
from app.services import strategy_profile as prof
from app.services.allocation_mix import current_mix
from app.services.intake_service import list_positions
from app.services.plan_service import get_plan
from app.services.strategy_service import apply_strategy, load_basket

router = APIRouter(prefix="/api/v1", tags=["strategy"])


@router.get("/strategies")
async def strategies(session: AsyncSession = Depends(get_session)) -> dict:
    # Static baskets carry a computed profile (expected return, vol, drawdown,
    # concentration) derived from a lookup table, so look-alike baskets are
    # visibly different. Rule-based strategies cannot be described that way --
    # theirs are *measured* by the nightly backtest and read from storage here.
    # This route never computes: a page load must not depend on a price provider.
    by_goal = {g: prof.with_profiles(v) for g, v in cat.by_goal().items()}
    # The fifth family is rule-based, so its numbers are measured rather than
    # derived. Cards are adapted to the shape the renderer already expects
    # (risk_tolerance, [ticker, weight] baskets) and carry `measured: true` so
    # the UI knows to read `backtest` instead of `profile`. A strategy with no
    # stored result still renders -- carrying backtest: null, so the card can
    # say "not measured yet" rather than drawing a blank where a number belongs.
    measured = await backtest_service.get_many(session, strategy_catalog.ids())
    by_goal[strategy_catalog.GOAL] = strategy_catalog.as_plan_cards(measured)
    return {"goals": [*cat.GOAL_ORDER, strategy_catalog.GOAL], "by_goal": by_goal,
            "backtest_engine_version": backtest_service.ENGINE_VERSION,
            # Not None means the measurements could not be read at all, which is
            # a different thing from "nothing has been computed yet".
            "backtest_store_error": backtest_service.store_unavailable}


@router.get("/strategies/backtests")
async def backtests(session: AsyncSession = Depends(get_session)) -> dict:
    """The Beat the Market family with its measured results. Never computes.

    Baskets are emitted as [ticker, weight] pairs to match the shape the Plan
    renderer already expects, so wiring the tab up is a UI change rather than a
    contract change.
    """
    rows = await backtest_service.get_many(session, strategy_catalog.ids())
    out = []
    for entry in strategy_catalog.CATALOG:
        out.append({**{k: v for k, v in entry.items()
                       if k not in ("weights", "base", "risk_off", "overlay")},
                    "goal": strategy_catalog.GOAL,
                    "basket": sorted((tk, w) for tk, w in entry.get("weights", {}).items()),
                    "base_when_flat": sorted(entry.get("base") or {}) or None,
                    "backtest": rows.get(entry["id"])})
    return {"goal": strategy_catalog.GOAL,
            "engine_version": backtest_service.ENGINE_VERSION,
            "store_error": backtest_service.store_unavailable,
            "strategies": out,
            "never_computed": [i for i in strategy_catalog.ids() if i not in rows],
            "stale": [k for k, v in rows.items() if v.get("stale")]}


@router.post("/strategies/backtests/refresh",
             dependencies=[Depends(require_role(Role.ANALYST))])
async def refresh_backtests(only: str | None = None,
                            session: AsyncSession = Depends(get_session)) -> dict:
    """Force a recompute (the nightly job does this at 03:30).

    Synchronous and slow -- it fetches ten years of daily closes per ticker.
    Exposed so a deploy does not have to wait until 03:30 for the first numbers.
    """
    ids = [s.strip() for s in only.split(",")] if only else None
    return await backtest_service.refresh_all(session, only=ids)


@router.get("/strategies/{strategy_id}/preview")
async def preview(strategy_id: str, sleeve_pct: float | None = None,
                  session: AsyncSession = Depends(get_session),
                  user: User = Depends(acting_user)) -> dict:
    """What changes if you apply this: objective, risk, target mix, plus trades."""
    # Rule-based strategies live in their own catalog; resolve either, so the
    # card's buttons work regardless of which family it came from.
    s = cat.get(strategy_id) or strategy_catalog.as_legacy_strategy(strategy_id, sleeve_pct)
    if not s:
        return {"ok": False, "error": "unknown strategy"}
    plan = await get_plan(session, user)
    rows = await list_positions(session, user)
    mix, nav = current_mix(rows)
    result = await apply_strategy_preview(session, user, s, plan, mix, nav)
    return {"ok": True, **result}


async def apply_strategy_preview(session, user, s, plan, mix, nav) -> dict:
    from app.engines.allocation_engine import AllocationEngine
    actions = []
    if nav > 0:
        report = AllocationEngine().compute(target_allocation=s["target_allocation"],
                                            current_allocation=mix, nav=nav)
        actions = [a.model_dump() for a in report.rebalance_actions]
    return {
        "strategy": {**s, "profile": prof.profile(s)},
        "diff": prof.diff_against_plan(s, plan, mix),
        "nav": round(nav, 2),
        "rebalance_actions": actions,
    }


@router.post("/strategies/{strategy_id}/apply", dependencies=[Depends(require_role(Role.ANALYST))])
async def apply(strategy_id: str, sleeve_pct: float | None = None,
                session: AsyncSession = Depends(get_session),
                user: User = Depends(acting_user)) -> dict:
    """Apply a strategy. ``sleeve_pct`` is how much of the book it governs.

    Omitted, a rule-based strategy falls back to its catalog-suggested sleeve
    rather than to 100%: putting an entire portfolio into a 3x fund because the
    model basket says so in isolation is not a default anyone wants.
    """
    return await apply_strategy(session, user, strategy_id, sleeve_pct)


class LoadBasketRequest(BaseModel):
    total: float | None = None
    # None means "use the sleeve already applied, else the catalog default" --
    # never 100%, which would quietly put the whole book in the aggressive leg.
    sleeve_pct: float | None = None


@router.post("/strategies/{strategy_id}/load-basket", dependencies=[Depends(require_role(Role.ANALYST))])
async def load(strategy_id: str, req: LoadBasketRequest | None = None,
               session: AsyncSession = Depends(get_session),
               user: User = Depends(acting_user)) -> dict:
    req = req or LoadBasketRequest()
    return await load_basket(session, user, strategy_id, total=req.total,
                             sleeve_pct=req.sleeve_pct)


@router.get("/strategies/signal")
async def strategy_signal(session: AsyncSession = Depends(get_session),
                          user: User = Depends(acting_user)) -> dict:
    """What the active rule-based strategy wants to hold today.

    Read-only: evaluating here never records a flip. A GET that consumed the
    signal would make "were you notified?" depend on whether you happened to
    open this page, and the daily job would have nothing left to report.

    Slow (it fetches recent closes for the strategy's tickers), so this is a
    diagnostic rather than something the app polls.
    """
    from app.services import strategy_signal_service as sigs
    return await sigs.peek_user(session, user)


@router.post("/strategies/signal/ack",
             dependencies=[Depends(require_role(Role.ANALYST))])
async def ack_strategy_signal(session: AsyncSession = Depends(get_session),
                              user: User = Depends(acting_user)) -> dict:
    """Clear a pending flip once you have acted on it (or decided not to)."""
    from app.services import strategy_signal_service as sigs
    sid = await sigs.active_strategy_id(session, user)
    if not sid:
        return {"ok": False, "error": "no rule-based strategy is applied"}
    return {"ok": await sigs.resolve_signal(session, user, sid), "strategy_id": sid}
