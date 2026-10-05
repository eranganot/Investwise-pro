"""Phase D - assemble the portfolio-risk report from live price history."""
from __future__ import annotations

import datetime as _dt
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.offload import offload
from app.engines.backtest_engine import BacktestEngine
from app.engines.portfolio_risk import analyze, pct_returns
from app.models.tables import User
from app.providers.registry import guarded_history, market_provider
from app.services.fx import position_value_ils
from app.services.intake_service import list_positions
from app.services.plan_service import get_plan

logger = logging.getLogger(__name__)


def _years_from_plan(plan) -> float | None:
    if plan is None:
        return None
    try:
        yr = int(str(getattr(plan, "target_date", "") or "")[:4])
        y = yr - _dt.date.today().year
        if y > 0:
            return float(y)
    except (ValueError, TypeError):
        pass
    h = getattr(plan, "horizon_years", None)
    return float(h) if h else None


async def portfolio_risk(session: AsyncSession, user: User, *, history_days: int = 252) -> dict:
    rows = await list_positions(session, user)
    if not rows:
        return {"ok": False, "reason": "no holdings"}

    tickers, weights, nav, maps = [], [], 0.0, {}
    for p in rows:
        # `position_value_ils`, not `quantity * price`. This was the sixth call
        # site of a formula whose own docstring says "the formula is not the
        # hard part; agreeing on it is" -- and it was the one that dropped the
        # rate, so the report valued a shekel book in dollars. Reconciled
        # against production 2026-09-04: NAV 7,437.82 against 21,207.78, which
        # is exactly the sum of the rows with the rate left off.
        value = await offload(position_value_ils, p)
        try:
            # One offload per holding, not one for the loop: each fetch is a
            # separate cached upstream call, and releasing the loop between
            # them is the whole point on a book with twenty names.
            series = await offload(guarded_history, p.ticker, history_days)  # [(date, close), ...]
        except Exception:
            series = []
        if len(series) < 3:
            continue
        tickers.append(p.ticker)
        weights.append(value)
        nav += value
        maps[p.ticker] = {d: c for d, c in series}
    if not tickers:
        return {"ok": False, "reason": "no usable price history for holdings"}

    cfg = get_settings()
    bench_map = None
    try:
        bench_map = {d: c for d, c in
                     await offload(guarded_history, cfg.benchmark_ticker, history_days)}
    except Exception:
        bench_map = None

    # Align every series on the dates common to ALL holdings (and the benchmark),
    # so correlations/beta are computed on matching trading days - not by length.
    common = set.intersection(*[set(m) for m in maps.values()])
    if bench_map:
        common &= set(bench_map)
    dates = sorted(common)
    if len(dates) < 3:
        return {"ok": False, "reason": "not enough overlapping price history"}
    hist = {t: [maps[t][d] for d in dates] for t in tickers}
    bench_rets = pct_returns([bench_map[d] for d in dates]) if bench_map else None

    plan = await get_plan(session, user)
    # PR 74 -- the ONE goal: the same target and the same years left to the
    # same deadline as /plan, the behind card and the projection. This read the
    # stored `plan.target_amount` and counted whole calendar years to the
    # deadline year (5 where /plan said 6).
    from app.services.goal_service import compute_goal, goal_from
    try:
        _g = await compute_goal(session, user, plan, nav)
    except Exception:  # noqa: BLE001 -- the ledger read must not cost the risk report
        logger.warning("goal for the risk report fell back to the saved amount", exc_info=True)
        _g = goal_from(plan, [], nav)
    target = _g.get("target")
    # Deadline set -> years LEFT to it; 0 (passed) means no forward window,
    # never a silent fall back to the horizon.
    years = ((_g.get("years_left") or None) if _g.get("deadline")
             else _years_from_plan(plan))

    # THE STATED FORWARD RETURN, which is what the goal projection is now built
    # from. `analyze` used to derive it from the trailing 251 days -- 107.08%/yr
    # on this book -- and push that five years through a GBM. `book_assumptions`
    # already produces a forward number with provenance, a basis, a coverage
    # percentage and a caveat that says in the user's own words that a measured
    # CAGR is not a forecast. Nothing was reading it here.
    #
    # Its `volatility_pct` comes along too, NOT to be used as the volatility --
    # the covariance figure is the right one and `analyze` keeps computing it --
    # but so the two can be compared where both exist. `/plan` has no price
    # history and giving it some would put N provider fetches on a hot endpoint;
    # this call already has the covariance and already reads the plan.
    drift_pct = assumed_vol_pct = None
    try:
        from app.services.measured_assumptions import book_assumptions
        _a = await book_assumptions(session, user)
        drift_pct = _a.get("expected_return_pct")
        # PR 98: one return basis -- the goal verdict's figure when it is current,
        # so this report's goal probability uses the same return as the cards.
        try:
            from app.services.frontier_service import book_expectation
            _fe = await book_expectation(session, user)
        except Exception:  # noqa: BLE001
            _fe = None
        if _fe:
            drift_pct = _fe["expected_return_pct"]
            _a = {**_a, "volatility_pct": _fe["volatility_pct"], "basis": "frontier"}
        # The blend as measured, not a rescaling of it. PR 55.1 passed a
        # sigma/sqrt(f) "in-market" version here so the comparison would come
        # out like-for-like, and it reconciled -- 68.02 against 67.48 -- by
        # cancellation rather than by being right. The scaling needs the
        # out-of-market days to be flat; `_weights_or_off` holds the sleeve's
        # BASE, never cash, and those days measure 22.46%. Withdrawn; see
        # STATUS.md B1. `analyze` now names the two candidate explanations for
        # the gap instead of asserting either.
        assumed_vol_pct = _a.get("volatility_pct")
    except Exception:  # noqa: BLE001 - a risk report must not fail on the assumptions
        drift_pct = assumed_vol_pct = None

    report = analyze(tickers=tickers, weights=weights, history_by_ticker=hist,
                     benchmark_returns=bench_rets, nav=nav, target=target, years=years,
                     drift_pct=drift_pct, assumed_volatility_pct=assumed_vol_pct)
    report["nav"] = round(nav, 2)
    report["benchmark"] = cfg.benchmark_ticker
    report["source"] = market_provider().name
    report["holdings_analyzed"] = tickers

    # Phase 3.3 cross-check: validate structural vs realized (historical) beta.
    if report.get("ok") and report.get("beta") is not None:
        bt = BacktestEngine().run(
            [{"ticker": t, "asset_class": "Equities", "value_ils": w}
             for t, w in zip(tickers, weights)],
            realized_beta=report["beta"])
        report["beta_validation"] = {"structural_beta": bt.structural_beta,
                                     "realized_beta": report["beta"],
                                     "validated": bt.beta_validated, "note": bt.critique}
    return report
