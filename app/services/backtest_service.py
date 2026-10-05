"""Precompute strategy backtests nightly; serve them from the database.

A backtest needs ten years of daily closes for every ticker a strategy touches.
Running that inside ``/strategies`` would hang a page load on a network fan-out
and make the strategy list fail whenever a price provider is down, so the work
happens on a schedule and the route only ever reads a stored row.

Three rules the storage layer enforces, all of them consequences of the same
idea -- a number on a card must be traceable to the run that produced it:

* A run that abstains records WHY, but does not destroy the previous
  measurement. The first cut overwrote it, reasoning that a figure which can no
  longer be reproduced should not present itself as current -- and then a single
  provider hiccup wiped all seven measurements at once and left nothing. Those
  are different failures: "this strategy is no longer measurable" versus "the
  price feed was down for a minute". The row keeps its last good numbers, marks
  the failed refresh, and reads as stale, so the UI can say "last measured on X,
  refresh failing since Y" instead of showing a blank.
* Every row carries ``engine_version``, the data source, the exact date span
  and the observation count. Bump the version and yesterday's rows are stale by
  definition rather than silently mixed with today's.
* ``computed_at`` drives a freshness flag. A stale row is still served -- an old
  measurement beats no measurement -- but it is labelled, never passed off as
  fresh.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.engines import strategy_backtest as bt
from app.models.tables import StrategyBacktest
from app.providers.registry import PRICE_ONLY, guarded_total_return_history, market_provider
from app.providers.resilience import CircuitOpenError, RateLimitedError
from app.services import strategy_catalog

logger = logging.getLogger(__name__)

# Bump when a change to the engine would alter a stored number OR add a field to
# one. Rows written by an older version are stale regardless of their age.
#
# Missed once already: a2 gained sessions_per_year, limiting_ticker and a
# benchmark-relative overfitting verdict without a bump, so rows computed by the
# previous engine kept reporting stale=false and were served as current. Every
# consumer then saw fields that were simply absent, which reads as "not
# measured" rather than "measured by an engine that did not have this field".
# The whole point of the version is that the two are distinguishable.
#
#   a2 -> a3: sessions_per_year, limiting_ticker, history_start_by_ticker,
#             history_capped_by_provider, and out-of-sample verdicts judged
#             against the benchmark's decay rather than against zero.
#   a3 -> a4: base_tickers / base_cagr_pct / base_max_drawdown_pct /
#             excess_over_base_cagr_pct -- the strategy measured against the
#             thing it levers, alongside the existing book benchmark. Also
#             benchmark_ticker, now recorded on the row (T0.1 / T0.4).
#   a4 -> a5: W2. `regime_gate_applied` on every row, and the headline numbers
#             for an opted-in strategy are now the GATED run. This changes what
#             a stored row MEANS, which is exactly what the version is for -- a
#             gated CAGR sitting in the same field as an ungated one, with
#             nothing to tell them apart, is worse than not having the gate.
#             The bump invalidates every row; the nightly job recomputes them.
#   a5 -> a6: PR 101. History is dividend-adjusted (total return), not plain
#             closes, so every CAGR moves up by the dividend yield and a bond
#             fund no longer measures as losing money. `return_basis` on every
#             result says which it was; a ticker with no adjusted series is
#             measured on plain closes and labelled `price_only`.
ENGINE_VERSION = "a6"

HISTORY_DAYS = 2600          # ~10y, the longest span served at daily granularity
STALE_AFTER_DAYS = 7
OOS_SPLIT = "2022-01-01"     # the only real bear market these instruments have seen

# What of the gated run is worth storing next to the headline. The full metrics
# dict again would double the row for numbers nobody compares.
_REGIME_KEYS = ("ok", "cagr_pct", "volatility_pct", "max_drawdown_pct",
                "excess_cagr_pct", "trades_per_year", "observations")


# A rate limit is a statement about US, not about the ticker. Retried with a
# widening pause before the symbol is written off; the sleep is a module-level
# name so a test can replace it and not actually wait.
FETCH_RETRIES = 3
FETCH_BACKOFF_SEC = 1.5
_sleep = time.sleep


def _fetch(tickers: list[str],
           classify: bool = False, basis: dict | None = None) -> tuple[dict, list[str]] | tuple[dict, list[str], list[str]]:
    """Price history per ticker.

    Returns `(series, no_data)` by default, and `(series, no_data, unavailable)`
    when `classify` is set. THE THIRD LIST IS THE POINT, and the default shape
    is kept only so the existing callers do not all have to change at once.

    A rate-limited or circuit-open call returns nothing for a reason that has
    NO BEARING ON THE TICKER, and this function used to record it identically to
    "this symbol has no price history" -- then log "backtest: no history for
    SPY", which is false about SPY and true about nothing. It is the same defect
    PR 10 removed from the screener, and PR 5b's liveness fix introduced there:
    dropping on any exception.

    It stopped being a corner case when T6b started fetching 26 catalog tickers
    in one pass. Measured on the first live run: 18 of 28 symbols came back
    "no history" -- SPY, VTI, QQQ, MSFT among them -- purely from rate limiting,
    and every one of the fourteen candidate cores was excluded as unmeasurable.
    A search that reports "none of your options can be measured" because it
    asked too quickly is worse than one that takes longer.
    """
    series: dict = {}
    no_data: list[str] = []
    unavailable: list[str] = []
    for tk in tickers:
        rows: list = []
        reason = None
        for attempt in range(FETCH_RETRIES):
            try:
                rows, tk_basis = guarded_total_return_history(tk, HISTORY_DAYS)
                if basis is not None:
                    basis[tk] = tk_basis
                reason = None
                break
            except (RateLimitedError, CircuitOpenError) as exc:
                reason = exc
                rows = []
                # Only pause when there is another attempt coming; the last
                # failure should not also cost the caller a sleep.
                if attempt < FETCH_RETRIES - 1:
                    _sleep(FETCH_BACKOFF_SEC * (attempt + 1))
            except Exception:  # noqa: BLE001
                reason = None          # a real answer of "nothing"
                rows = []
                break
        if len(rows) >= 3:
            series[tk] = rows
        elif reason is not None:
            logger.warning("backtest: %s unavailable (%s) after %d attempts - "
                           "this is not a statement about the ticker",
                           tk, type(reason).__name__, FETCH_RETRIES)
            unavailable.append(tk)
        else:
            logger.warning("backtest: no history for %s", tk, exc_info=False)
            no_data.append(tk)
    if classify:
        return series, no_data, unavailable
    # The legacy contract: callers that cannot yet tell the two apart still get
    # both in one list, because treating an unavailable ticker as present would
    # be worse than treating it as missing.
    return series, sorted(no_data + unavailable)


def return_basis_of(basis: dict) -> dict:
    """`return_basis` for a measurement: total return only if every ticker in it
    was dividend-adjusted. One price-only ticker makes the whole run price-only,
    and the run names it, so a card can say which figure is missing dividends."""
    price_only = sorted(tk for tk, b in basis.items() if b == PRICE_ONLY)
    return {"return_basis": PRICE_ONLY if price_only else "total_return",
            "price_only_tickers": price_only}


def measure(spec: dict, *, benchmark_ticker: str | None = None) -> dict:
    """Run one strategy end to end: fetch, backtest, and probe its fragility."""
    bench_tk = benchmark_ticker or get_settings().benchmark_ticker
    # Fetch the regime indexes too, and hand the SAME series to both runs, so the
    # gated and ungated numbers share one date window and the comparison is
    # apples to apples. (In practice SPY/QQQ/IWM all outlive the strategy
    # tickers, so including them does not move the ungated window -- a test
    # asserts the two runs agree on `observations` rather than trusting that.)
    needed = bt.tickers_needed(spec, regime_gate=True)
    basis: dict = {}
    series, missing = _fetch(sorted(set(needed) | {bench_tk}), basis=basis)
    if missing:
        return {"ok": False, "reason": bt.MISSING_TICKER,
                "detail": f"no price history for {', '.join(missing)}"}
    bench = series.pop(bench_tk, None) if bench_tk not in needed else series.get(bench_tk)
    result = bt.run(series, spec, benchmark=bench)
    if not result.get("ok"):
        return result
    # Which benchmark this measurement is relative to. Stored on the row so a
    # later change to settings.benchmark_ticker marks it stale instead of
    # silently relabelling a SPY-measured excess as excess-over-QQQ.
    result["benchmark_ticker"] = bench_tk
    result.update(return_basis_of(basis))

    # Fragility travels with the headline number rather than being computed once
    # and forgotten: a strategy tuned until it looked good shows up here.
    robustness: dict = {}
    try:
        robustness["out_of_sample"] = bt.out_of_sample(
            series, spec, split_date=OOS_SPLIT, benchmark=bench)
    except Exception:  # noqa: BLE001
        logger.warning("backtest: out-of-sample failed for %s", spec.get("id"), exc_info=False)
    sweep_param = (spec.get("overlay") or {}).get("sweep_param")
    sweep_values = (spec.get("overlay") or {}).get("sweep_values")
    if sweep_param and sweep_values:
        try:
            robustness["sweep"] = bt.sweep(series, spec, sweep_param, list(sweep_values),
                                           benchmark=bench)
        except Exception:  # noqa: BLE001
            logger.warning("backtest: sweep failed for %s", spec.get("id"), exc_info=False)
    # Does a regime gate improve this strategy? Measured, reported, and NOT
    # switched on: the gate ships off everywhere until a human has compared the
    # two numbers. Auto-enabling whatever measured better would be selecting on
    # one sample of history, which is what the overfitting flag exists to catch.
    cfg = get_settings()
    opted_in = {s.strip() for s in (cfg.regime_gate_enabled_for or "").split(",") if s.strip()}
    enabled = spec.get("id") in opted_in
    try:
        gated = bt.run(series, spec, benchmark=bench, regime_gate=True)
        robustness["regime"] = {
            # The LIMIT is the user's now. `gate_verdict` reports which one it
            # used and whether that was the default, so a verdict can never be
            # read without knowing the threshold that produced it.
            "verdict": bt.gate_verdict(result, gated,
                                       max_giveup_pct=cfg.regime_gate_max_cagr_giveup_pct),
            "gated": ({k: v for k, v in gated.items() if k in _REGIME_KEYS}
                      if gated.get("ok") else
                      {"ok": False, "reason": gated.get("reason"),
                       "detail": gated.get("detail")}),
            # STILL FALSE BY DEFAULT. The rule above is unchanged: the gate ships
            # off until a human has compared the two numbers. What changed is
            # that there is now a way for a human to say they have, and it is a
            # per-strategy setting rather than a code edit.
            "enabled": enabled,
            "ungated": {k: v for k, v in result.items() if k in _REGIME_KEYS},
        }
        # THE HEADLINE SWAPS ONLY FOR AN OPTED-IN STRATEGY, and only when the
        # gated run actually measured. Falling back to the ungated numbers
        # silently would put an ungated CAGR under a row that claims to be
        # gated, which is the confusion `regime_gate_applied` exists to prevent.
        if enabled and gated.get("ok"):
            gated["robustness"] = robustness
            gated["regime_gate_applied"] = True
            robustness["regime"]["headline_is_gated"] = True
            return gated
        if enabled:
            robustness["regime"]["headline_is_gated"] = False
            robustness["regime"]["opt_in_not_honoured"] = (
                f"this strategy is opted in, but the gated run abstained "
                f"({gated.get('reason')}), so the numbers below are UNGATED")
            logger.warning("backtest: %s is opted into the regime gate but the "
                           "gated run abstained (%s); serving ungated numbers",
                           spec.get("id"), gated.get("reason"))
    except Exception:  # noqa: BLE001
        logger.warning("backtest: regime comparison failed for %s", spec.get("id"),
                       exc_info=False)

    result["regime_gate_applied"] = False
    result["robustness"] = robustness
    return result


# ---------------------------------------------------------------------------
# PR 37 -- a core is a strategy too
# ---------------------------------------------------------------------------
#
# Eran, 2026-09-02: *"the sleeves have their strategy and the core has its own
# strategy -- the portfolio is being managed by two different strategies."*
#
# He is right, and until now only one of the two was ever measured. `measure()`
# above walks `strategy_catalog`, which holds RULE-BASED sleeves. The static
# families in `strategies.CATALOG` -- the ones that can stand as a core -- were
# never backtested at all, so `book_assumptions` had no row to read and fell
# back to the per-ticker instrument table for the entire core.
#
# THAT FALLBACK IS NOT NEUTRAL. A per-ticker table has no covariance term, so
# pricing a rebalanced 60/40 as the weighted average of a VTI row and a BND row
# OVERSTATES ITS VOLATILITY BY CONSTRUCTION -- the one thing rebalancing between
# two weakly-correlated legs does is the one thing the table cannot express.
#
# Nothing new is invented here. `blend.core_spec` turns a basket into a
# buy-and-hold spec and `blend.measure_blend` runs it through the same
# `targets_for` -> `_simulate` -> `_metrics` path every sleeve takes, which is
# how `core_search` already ranks fourteen candidate cores. This wires that to
# the nightly job and the same `StrategyBacktest` table, so a core passes
# `usable()` on exactly the terms a sleeve does.

# A fixed basket has no fitted parameter, so it gets no out-of-sample fold. This
# is a REASON, not a failure, and it is stored as one.
NO_FIT_TO_TEST = "NO_FIT_TO_TEST"


def measure_cores(candidates: list[dict] | None = None) -> dict[str, dict]:
    """Every static core family, measured as the buy-and-hold basket it is.

    ONE FETCH for the union of every basket plus the benchmark. Fourteen
    separate fetches of a heavily overlapping ticker set is how a run gets
    rate-limited into reporting that none of the cores can be measured -- the
    exact failure `_fetch`'s own docstring records from the first live T6b run.

    BUT EACH BASKET IS MEASURED ON A FEED SCOPED TO ITS OWN TICKERS. `align()`
    intersects the DATE AXIS across everything in the feed, so one late-listing
    ticker in one basket would silently truncate all fourteen to its history and
    report ten-year figures for none of them. `target_solver._core_blocking`
    documents that trap at length; this is the same trap and the same answer.

    A basket whose own tickers are missing is excluded BY NAME, and the other
    thirteen are still measured.
    """
    from app.engines import blend
    from app.services import strategies as static_cat

    cands = candidates if candidates is not None else static_cat.core_candidates()
    if not cands:
        return {}

    bench_tk = get_settings().benchmark_ticker
    wanted = {bench_tk}
    for c in cands:
        wanted |= {t.upper() for t in (c.get("weights") or {})}
    basis: dict = {}
    series, no_data, unavailable = _fetch(sorted(wanted), classify=True, basis=basis)
    # The two kinds of absence stay apart to the end: "BND has no price history"
    # is a fact about BND, "BND was rate-limited" is a fact about this run, and
    # a reader who cannot tell them apart goes off to check for a delisting.
    missing_set, unavail_set = set(no_data), set(unavailable)
    bench = series.get(bench_tk)

    out: dict[str, dict] = {}
    for c in cands:
        sid = c.get("id")
        need = {t.upper() for t in (c.get("weights") or {})}
        if not need:
            out[sid] = {"ok": False, "reason": "NO_WEIGHTS",
                        "detail": "this family carries no basket"}
            continue
        short = sorted(need & (missing_set | unavail_set))
        if short:
            hard = [t for t in short if t in missing_set]
            out[sid] = (
                {"ok": False, "reason": bt.MISSING_TICKER,
                 "detail": f"no price history for {', '.join(hard)}"}
                if hard else
                {"ok": False, "reason": "PROVIDER_UNAVAILABLE",
                 "detail": (f"{', '.join(short)} could not be fetched on this run; "
                            f"that is a fact about the run, not about the ticker")})
            continue
        feed = {tk: series[tk] for tk in need if tk in series}
        try:
            res = blend.measure_blend(
                feed,
                [{"id": sid, "spec": blend.core_spec(c["weights"]), "weight": 1.0}],
                benchmark=bench, detail=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("backtest: core %s blew up", sid, exc_info=True)
            res = {"ok": False, "reason": "ENGINE_ERROR", "detail": str(e)[:255]}
        if res.get("ok"):
            # Which benchmark this is relative to, for the same reason `measure`
            # records it: a later settings change must mark the row stale rather
            # than relabel a SPY-measured excess as excess-over-QQQ.
            res["benchmark_ticker"] = bench_tk
            res.update(return_basis_of({tk: basis.get(tk) for tk in need | {bench_tk}}))
            # A BUY-AND-HOLD BASKET HAS NOTHING TO FIT, SO IT GETS NO
            # OUT-OF-SAMPLE FOLD. `measure()` stores one for a rule because a
            # rule has parameters that can be tuned until the past looks good.
            # A fixed 60/40 has no parameter, so splitting its history would
            # measure the decade rather than the strategy. Recording that as a
            # named reason beats leaving `robustness` empty and letting a reader
            # take the silence for a failed measurement.
            res["robustness"] = {"out_of_sample": {
                "ok": False, "reason": NO_FIT_TO_TEST,
                "detail": ("a fixed basket has no fitted parameter, so an "
                           "out-of-sample split would measure the window, "
                           "not the strategy")}}
            res["is_core_basket"] = True
        out[sid] = res
    return out


async def refresh_cores(session: AsyncSession, *,
                        only: list[str] | None = None) -> dict:
    """Measure and store every static core family. Part of the nightly job."""
    from app.services import strategies as static_cat

    wanted = set(only) if only else None
    cands = [c for c in static_cat.core_candidates()
             if wanted is None or c["id"] in wanted]
    results = measure_cores(cands)
    done = 0
    for sid, res in results.items():
        await store(session, sid, res)
        if res.get("ok"):
            done += 1
    return {"computed": done, "abstained": len(results) - done,
            "ids": sorted(results)}


async def store(session: AsyncSession, strategy_id: str, result: dict) -> StrategyBacktest:
    """Upsert one result. An abstention replaces the previous metrics, never hides behind them."""
    row = (await session.execute(
        select(StrategyBacktest).where(StrategyBacktest.strategy_id == strategy_id)
    )).scalar_one_or_none()
    if row is None:
        row = StrategyBacktest(strategy_id=strategy_id)
        session.add(row)
    ok = bool(result.get("ok"))
    row.engine_version = ENGINE_VERSION
    row.ok = ok
    row.reason = "" if ok else str(result.get("reason") or "")[:32]
    row.detail = "" if ok else str(result.get("detail") or "")[:255]
    if ok:
        row.metrics = {k: v for k, v in result.items() if k != "robustness"}
        row.robustness = result.get("robustness") or {}
        row.last_error = ""
        row.last_error_at = None
    else:
        # Keep the last good measurement. A transient provider failure must not
        # be able to erase ten years of computed history -- the row goes stale
        # and says why, which is recoverable; a wiped row is not.
        row.last_error = f"{row.reason}: {row.detail}"[:255]
        row.last_error_at = datetime.now(timezone.utc)
    if ok:
        row.data_source = (f"{market_provider().name}:"
                           f"{result.get('return_basis') or 'price_only'}")[:32]
        row.period_start = str(result.get("start") or "")[:10]
        row.period_end = str(result.get("end") or "")[:10]
        row.benchmark_ticker = str(result.get("benchmark_ticker") or "")[:16]
    if ok:
        row.observations = int(result.get("observations") or 0)
        row.computed_at = datetime.now(timezone.utc)
    return row


def _benchmark_changed(row: StrategyBacktest) -> bool:
    """The row was measured against a benchmark the settings no longer use.

    An excess is only meaningful against the thing it was measured against.
    Serving a SPY-measured `excess_cagr_pct` on a screen that now says QQQ is
    not a stale number -- it is a wrong one. An empty column means the row
    predates this field; the engine_version bump already marks those stale, so
    it is not treated as a mismatch here.
    """
    stored = (row.benchmark_ticker or "").upper()
    return bool(stored) and stored != (get_settings().benchmark_ticker or "").upper()


def _is_stale(row: StrategyBacktest) -> bool:
    if row.engine_version != ENGINE_VERSION:
        return True
    if _benchmark_changed(row):
        return True
    when = row.computed_at
    if when is None:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when) > timedelta(days=STALE_AFTER_DAYS)


def _payload(row: StrategyBacktest) -> dict:
    return {
        # Named explicitly so a consumer can say WHY a row is stale. "Older than
        # a week" and "produced by a different engine" need different responses:
        # the first waits for tonight's job, the second needs a recompute now.
        "stale_reason": (None if not _is_stale(row) else
                         "engine_version" if row.engine_version != ENGINE_VERSION
                         else "benchmark" if _benchmark_changed(row)
                         else "age"),
        "live_engine_version": ENGINE_VERSION,
        "ok": row.ok,
        "reason": row.reason or None,
        "detail": row.detail or None,
        "metrics": row.metrics or {},
        # A row can carry good numbers AND a failing refresh at the same time.
        # Collapsing those into one "ok" hides the difference between "never
        # measurable" and "measured, but the feed is down right now".
        "last_error": row.last_error or None,
        "last_error_at": (row.last_error_at.isoformat() if row.last_error_at else None),
        "refresh_failing": bool(row.last_error),
        "robustness": row.robustness or {},
        "engine_version": row.engine_version,
        "data_source": row.data_source,
        "period": {"start": row.period_start, "end": row.period_end,
                   "observations": row.observations},
        "benchmark_ticker": row.benchmark_ticker or None,
        # Every figure travels with the window it was measured over, and with
        # what KIND of measurement it is. A ten-year strategy backtest and a
        # 250-day holdings backfill will disagree, and both are correct -- but
        # only if the screen can tell them apart. `kind` is what lets a renderer
        # refuse to put two different measurements on one unlabelled line.
        "window": {"start": row.period_start, "end": row.period_end,
                   "sessions": row.observations, "kind": "strategy_backtest"},
        "computed_at": row.computed_at.isoformat() if row.computed_at else None,
        "stale": _is_stale(row),
    }


# Set when the last read of the store failed, so a caller can tell "no backtests
# have been computed yet" from "the store could not be read at all".
store_unavailable: str | None = None


async def get_many(session: AsyncSession, strategy_ids: list[str]) -> dict[str, dict]:
    """Stored results keyed by strategy id. Never computes -- the route must not block.

    Returns {} rather than raising when the table cannot be read. A strategy
    LIST should not die because a nightly job's storage is behind: the four
    original families need no backtest at all, and the measured ones can say
    "not measured yet" perfectly well.

    Observed in production: migration 0011 added last_error / last_error_at, the
    deploy shipped before `alembic upgrade head` ran, and every SELECT here hit
    an undefined column -- which took out GET /strategies and
    /strategies/backtests entirely, and degraded the recommendations pipeline
    through discipline_recs. A missing column in an optional side table should
    never be able to do that.

    The read runs in a SAVEPOINT so that on Postgres a failure here does not
    abort the caller's transaction and take down everything after it.
    """
    global store_unavailable
    if not strategy_ids:
        return {}
    try:
        async with session.begin_nested():
            rows = (await session.execute(
                select(StrategyBacktest).where(StrategyBacktest.strategy_id.in_(strategy_ids))
            )).scalars().all()
        store_unavailable = None
        return {r.strategy_id: _payload(r) for r in rows}
    except Exception as e:  # noqa: BLE001
        store_unavailable = f"{type(e).__name__}: {str(e)[:180]}"
        logger.warning("backtest store unreadable; serving strategies without measurements: %s",
                       store_unavailable, exc_info=False)
        return {}


async def refresh_all(session: AsyncSession, *, only: list[str] | None = None) -> dict:
    """Recompute every backtestable strategy. Called by the nightly job."""
    specs = strategy_catalog.backtestable(only=only)
    done, failed = 0, 0
    for spec in specs:
        try:
            result = measure(spec)
        except Exception as e:  # noqa: BLE001
            logger.warning("backtest: %s blew up", spec.get("id"), exc_info=True)
            result = {"ok": False, "reason": "ENGINE_ERROR", "detail": str(e)[:255]}
        await store(session, spec["id"], result)
        if result.get("ok"):
            done += 1
        else:
            failed += 1
    # THE CORES, in the same run and the same table. A book is managed by two
    # kinds of strategy and the nightly job used to measure one of them.
    cores = {"computed": 0, "abstained": 0, "ids": []}
    try:
        cores = await refresh_cores(session)
    except Exception:  # noqa: BLE001
        logger.warning("backtest: core refresh failed", exc_info=True)
    await session.commit()
    # Every strategy failing on price history at once is one provider outage,
    # not seven broken strategies -- and the resilience tier's circuit breaker
    # makes that the normal shape of a bad minute, since it opens for the whole
    # "history" tier at once. Saying so stops a run that should be retried from
    # reading as a catalog that fell apart.
    outage = bool(specs) and done == 0 and failed == len(specs)
    # Sleeves and cores are counted TOGETHER in the headline, because the caller
    # asked "did the nightly measurement work", and separately underneath,
    # because "every sleeve measured and every core failed" is a different
    # morning from "everything measured".
    return {"computed": done + cores["computed"],
            "abstained": failed + cores["abstained"],
            "sleeves": {"computed": done, "abstained": failed},
            "cores": cores,
            "engine_version": ENGINE_VERSION,
            "provider_outage": outage,
            "note": ("every strategy failed to fetch prices - likely a provider "
                     "outage or an open circuit breaker, not a catalog problem; "
                     "previous measurements were kept" if outage else None)}


async def _refresh_all_job() -> dict:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as session:
            return await refresh_all(session)
    finally:
        await engine.dispose()


def run_backtest_refresh_blocking() -> dict:
    """Sync entrypoint for APScheduler (runs in its own thread)."""
    try:
        res = asyncio.run(_refresh_all_job())
        logger.info("backtest refresh: %s", res)
        return res
    except Exception:  # noqa: BLE001
        logger.warning("scheduled backtest refresh failed", exc_info=True)
        return {"computed": 0, "abstained": 0}


# ---------------------------------------------------------------------------
# W1 -- walk-forward parameter sweeps
# ---------------------------------------------------------------------------
#
# `measure()` above already runs `bt.sweep()` nightly for every overlay that
# declares one, and stores the result in `StrategyBacktest.robustness`. Nothing
# reads it: grepping every renderer, route and script, `robustness.sweep` has
# zero consumers. That sweep is also FULL-SAMPLE and selects nothing -- it
# reports the spread of CAGRs as a fragility flag and stops there.
#
# This is the other thing. It splits, it selects, and it checks the selection
# against the value already in the catalog.

def _window(series: dict, lo: int, hi: int) -> dict:
    """Restrict every ticker to the common sessions [lo, hi).

    Sliced on the COMMON axis, computed once, because slicing each ticker's own
    rows and intersecting afterwards yields fewer sessions than asked whenever
    two tickers trade on different calendars -- and the shortfall would not be
    attributable to anything. Same argument as `target_solver._tail`.
    """
    common = None
    for rows in series.values():
        dates = {d for d, _ in rows}
        common = dates if common is None else (common & dates)
    if not common:
        return {tk: [] for tk in series}
    axis = sorted(common)
    keep = set(axis[lo:hi])
    return {tk: [(d, c) for d, c in rows if d in keep] for tk, rows in series.items()}


def walk_forward_blocking(spec: dict, *, benchmark_ticker: str | None = None) -> dict:
    """One strategy, swept walk-forward. Blocking; offload it."""
    from app.services import walk_forward as wf

    overlay = spec.get("overlay") or {}
    param = overlay.get("sweep_param")
    values = list(overlay.get("sweep_values") or [])
    if not param or not values:
        # NAMED, not omitted. `btm_factor_stack` is buy_hold and correctly has no
        # parameter; a strategy that silently vanished from a report would read
        # as one that was measured and found wanting.
        return {"ok": False, "reason": "NO_SWEEP",
                "detail": f"{spec.get('id')} declares no sweep_param; "
                          f"there is no parameter to walk forward"}

    default_value = overlay.get(param)
    if default_value is None:
        return {"ok": False, "reason": "NO_DEFAULT",
                "detail": f"{spec.get('id')} sweeps {param} but does not set it, so "
                          f"there is no 'what you already run' to compare against"}

    bench_tk = benchmark_ticker or get_settings().benchmark_ticker
    needed = sorted(set(bt.tickers_needed(spec)) | {bench_tk})
    series, no_data, unavailable = _fetch(needed, classify=True)
    gone = set(no_data) | set(unavailable)
    if gone:
        return {"ok": False, "reason": bt.MISSING_TICKER,
                "detail": f"no usable price history for {', '.join(sorted(gone))}",
                "no_data_tickers": sorted(no_data),
                "unavailable_tickers": sorted(unavailable)}

    bench_rows = series.get(bench_tk)
    feed = {tk: rows for tk, rows in series.items() if tk != bench_tk} or series

    common = None
    for rows in feed.values():
        dates = {d for d, _ in rows}
        common = dates if common is None else (common & dates)
    n_sessions = len(common or ())
    fold_list = wf.folds(n_sessions)
    if not fold_list:
        return {"ok": False, "reason": bt.INSUFFICIENT_HISTORY,
                "detail": (f"{n_sessions} common sessions; one fold needs "
                           f"{wf.FIT_SESSIONS + wf.TEST_SESSIONS}")}

    # RANK KEY DECIDED ONCE, per split_solver's invariant: ranking some folds on
    # excess and others on raw CAGR would compare two questions.
    rank_key = "excess_cagr_pct" if bench_rows else "cagr_pct"

    def measure(value, fold, phase):
        lo, hi = ((fold["fit_from"], fold["fit_to"]) if phase == "fit"
                  else (fold["test_from"], fold["test_to"]))
        trial = {**spec, "overlay": {**overlay, param: value}}
        sub = _window(feed, lo, hi)
        b = None
        if bench_rows:
            dates = {d for d, _ in next(iter(sub.values()), [])}
            b = [(d, c) for d, c in bench_rows if d in dates]
        m = bt.run(sub, trial, benchmark=b)
        if not m.get("ok"):
            return None
        return m.get(rank_key)

    out = wf.walk_forward(values, default_value, fold_list, measure)
    out["strategy_id"] = spec.get("id")
    out["param"] = param
    out["benchmark"] = bench_tk if bench_rows else None
    out["ranked_on"] = rank_key
    out["sessions_available"] = n_sessions
    untested = n_sessions - out["coverage"]["tested_through"]
    out["coverage"]["sessions_available"] = n_sessions
    out["coverage"]["sessions_untested_at_end"] = untested
    out["coverage"]["untested_note"] = (
        f"{untested} sessions at the RECENT end are in no test window. "
        f"{n_sessions} does not divide into whole folds, and extending the last "
        f"one would make it shorter than the rest -- which breaks the comparability "
        f"the fixed fit length exists for. Left out and named rather than absorbed."
        if untested > 0 else "every session after the first fit window was tested")
    # investing-discipline SS5.
    out["disclaimer"] = ("A measurement of your own configured strategy over the "
                         "windows named. Nothing here changes any parameter. "
                         "Not financial advice.")
    return out
