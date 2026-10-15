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
    # Beat the Market leads. It is the family this app is actually for -- the
    # four static goals are the conservative baseline, not the headline -- and
    # goals[0] is what the Plan tab opens on.
    return {"goals": [strategy_catalog.GOAL, *cat.GOAL_ORDER], "by_goal": by_goal,
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
                    # PR 125: the tier the measurement puts it in. `risk` stays
                    # the designed label so the two can be compared.
                    "measured_risk": strategy_catalog.risk_label(entry, rows.get(entry["id"])),
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


@router.get("/strategies/correlations")
async def strategy_correlations(session: AsyncSession = Depends(get_session),
                                user: User = Depends(acting_user)) -> dict:
    """PR 132 (D97): how closely each catalogue pair moves, as last measured, and
    which pairs are the same bet under your limits. Never computes."""
    from app.services import sleeve_diversity as div
    corr = await div.load_correlations(session)
    ids = strategy_catalog.active_ids()
    rows = []
    for n, a in enumerate(ids):
        for b in ids[n + 1:]:
            ov, markets = div.overlap_pct(div._spec_of(a), div._spec_of(b))
            rho = (corr.get("pairs") or {}).get(div.pair_key(a, b))
            sb = div.same_bet(a, b, corr)
            rows.append({"a": a, "b": b, "a_name": div._name(a), "b_name": div._name(b),
                         "correlation": rho, "overlap_pct": ov, "markets": markets,
                         "same_bet": sb is not None, "why": sb["why"] if sb else None})
    rows.sort(key=lambda r: -(r["correlation"] if r["correlation"] is not None else -2))
    lim_o, lim_c = div._limits()
    return {"ok": bool(corr), "as_of": corr.get("as_of"), "overlap_limit_pct": lim_o,
            "correlation_limit": lim_c, "pairs": rows,
            "unmeasured": corr.get("unmeasured") or [], "missing_tickers": corr.get("missing_tickers") or []}


@router.get("/strategies/diversifiers")
async def diversifier_candidates(user: User = Depends(acting_user)) -> dict:
    """PR 133: the 13 wave-3 candidates as the owner approved them (not offered as sleeves)."""
    from app.services import diversifier_candidates as dc
    return {"candidates": [{k: c.get(k) for k in ("code", "id", "family", "name", "expected_risk",
                                                  "sleeve_pct", "short_history")} | {"spec": dc.spec(c["id"])}
                           for c in dc.CANDIDATES]}


@router.get("/strategies/diversifiers/near-duplicates")
async def diversifier_near_duplicates(user: User = Depends(acting_user)) -> dict:
    """PR 133, gate G6: candidate pairs whose daily returns correlate above 0.90. Read-only."""
    from app.core.offload import offload
    from app.services import diversifier_candidates as dc
    from app.services import diversifier_gate as gate
    return await offload(gate.near_duplicates_blocking, [c["id"] for c in dc.CANDIDATES])


@router.get("/strategies/diversifiers/{key}/measure")
async def diversifier_measure(key: str, session: AsyncSession = Depends(get_session),
                              user: User = Depends(acting_user)) -> dict:
    """PR 133: one candidate against the wave-3 gate (B1, G2-G5, G1 badge) on your book.

    Slow -- ten years of prices, the long history, a five-point sweep and two book
    simulations. Read-only: nothing is stored, offered or traded."""
    from app.core.offload import offload
    from app.services import book_effect
    from app.services import diversifier_gate as gate
    from app.services.target_solver import _book_for_solve

    book, err = await _book_for_solve(session, user)
    rg, dt = book_effect.limits()
    out = await offload(gate.evaluate_blocking, key, None if err else book,
                        ratio_gain=rg, drop_tol=dt)
    if err and isinstance(out, dict):
        out["book_unavailable"] = err
    return out


@router.post("/strategies/correlations/refresh",
             dependencies=[Depends(require_role(Role.ANALYST))])
async def refresh_strategy_correlations(session: AsyncSession = Depends(get_session)) -> dict:
    """Measure every catalogue pair now (the nightly job runs at 03:50). Slow: ten
    years of closes for every catalogue ticker. Stores the matrix; trades nothing."""
    from app.services import sleeve_diversity as div
    out = await div.refresh_correlations(session)
    return {"stored": out.get("stored"), "as_of": out.get("as_of"),
            "pairs": len(out.get("pairs") or {}), "unmeasured": out.get("unmeasured") or [],
            "missing_tickers": out.get("missing_tickers") or []}


@router.get("/strategies/candidates/book-effect")
async def candidates_book_effect(session: AsyncSession = Depends(get_session),
                                 user: User = Depends(acting_user)) -> dict:
    """PR 129: every pre-registered candidate's effect on YOUR book. Read-only, slow
    (one blended ten-year simulation per candidate, twice). Declared before the
    `{strategy_id}` routes so "candidates" is never read as an id."""
    from app.services import book_effect

    return await book_effect.for_all_candidates(session, user)


@router.get("/strategies/{strategy_id}/book-effect")
async def strategy_book_effect(strategy_id: str, sleeve_pct: float | None = None,
                               session: AsyncSession = Depends(get_session),
                               user: User = Depends(acting_user)) -> dict:
    """PR 129: what adding this strategy does to the whole book. Read-only."""
    from app.services import book_effect

    return await book_effect.for_strategy(session, user, strategy_id, sleeve_pct)


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
    result = await apply_strategy_preview(session, user, s, plan, mix, nav,
                                          strategy_id=strategy_id, sleeve_pct=sleeve_pct)
    return {"ok": True, **result}


async def apply_strategy_preview(session, user, s, plan, mix, nav, *,
                                 strategy_id: str | None = None,
                                 sleeve_pct: float | None = None) -> dict:
    from app.engines.allocation_engine import AllocationEngine
    actions = []
    if nav > 0:
        report = AllocationEngine().compute(target_allocation=s["target_allocation"],
                                            current_allocation=mix, nav=nav)
        actions = [a.model_dump() for a in report.rebalance_actions]

    # For a rule-based strategy the asset-class actions are the wrong answer and
    # usually an empty one: the book is already ~88% equities against a 100%
    # target, so "what changes?" showed nothing and applying read as a no-op.
    # What the user is actually asking is "what do I need to get rid of?" -- so
    # show the funding plan. Reuses the sleeve sizing from P0.1 rather than
    # growing a second implementation that can disagree with the button.
    funding = None
    sleeve_cap = None
    if strategy_id and strategy_catalog.get(strategy_id) is not None:
        from app.services.strategy_service import load_basket, sleeve_targets
        try:
            funding = await load_basket(session, user, strategy_id,
                                        sleeve_pct=sleeve_pct, mode="fund", dry_run=True)
        except Exception:  # noqa: BLE001 -- a preview must never break the Plan page
            funding = None
        _t = sleeve_targets(strategy_id, sleeve_pct if sleeve_pct is not None
                            else s.get("sleeve_pct"))
        sleeve_cap = [{"ticker": tk, "level_pct": round(w * 100, 1)}
                      for tk, w in sorted(_t.items()) if w > 0]

    return {
        "strategy": {**s, "profile": prof.profile(s)},
        "diff": prof.diff_against_plan(s, plan, mix),
        "nav": round(nav, 2),
        "rebalance_actions": actions,
        # What applying would arm, and what funding it would cost. Both read-only.
        "sleeve_cap": sleeve_cap,
        "funding": funding,
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
    # "fund" raises only the sleeve's shortfall and leaves every other holding
    # alone; "replace" deletes the book and installs the model basket. None picks
    # the right default per family: rule-based strategies are sleeves, the four
    # static families are model portfolios. Callers must be explicit to destroy
    # anything they were not defaulted into.
    mode: str | None = None
    # Compute and return the plan without writing. The confirm dialog uses this,
    # so "Fund this sleeve" can name every leg and its tax cost before anything
    # is sold.
    dry_run: bool = False


@router.post("/strategies/{strategy_id}/load-basket", dependencies=[Depends(require_role(Role.ANALYST))])
async def load(strategy_id: str, req: LoadBasketRequest | None = None,
               session: AsyncSession = Depends(get_session),
               user: User = Depends(acting_user)) -> dict:
    req = req or LoadBasketRequest()
    return await load_basket(session, user, strategy_id, total=req.total,
                             sleeve_pct=req.sleeve_pct, mode=req.mode,
                             dry_run=req.dry_run)


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
async def ack_strategy_signal(strategy_id: str | None = None,
                              session: AsyncSession = Depends(get_session),
                              user: User = Depends(acting_user)) -> dict:
    """Clear a pending flip once you have acted on it (or decided not to).

    ``strategy_id`` names the sleeve. Omit it and every pending flip is cleared,
    which is the honest reading of an un-targeted "I have dealt with it" on a
    book running several sleeves -- the alternative was silently clearing
    whichever sleeve happened to be first and leaving the rest armed.
    """
    from app.services import strategy_signal_service as sigs

    ids = await sigs.active_strategy_ids(session, user)
    if not ids:
        return {"ok": False, "error": "no rule-based strategy is applied"}
    if strategy_id is not None:
        if strategy_id not in ids:
            return {"ok": False, "error": f"'{strategy_id}' is not a sleeve on this book"}
        ids = [strategy_id]
    cleared = [sid for sid in ids if await sigs.resolve_signal(session, user, sid)]
    return {"ok": bool(cleared), "cleared": cleared,
            # Kept for callers written against the single-sleeve shape.
            "strategy_id": cleared[0] if cleared else None}
