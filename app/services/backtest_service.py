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
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.engines import strategy_backtest as bt
from app.models.tables import StrategyBacktest
from app.providers.registry import guarded_history, market_provider
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
ENGINE_VERSION = "a3"

HISTORY_DAYS = 2600          # ~10y, the longest span served at daily granularity
STALE_AFTER_DAYS = 7
OOS_SPLIT = "2022-01-01"     # the only real bear market these instruments have seen


def _fetch(tickers: list[str]) -> tuple[dict, list[str]]:
    """Price history per ticker; the second element is what could not be fetched."""
    series, missing = {}, []
    for tk in tickers:
        try:
            rows = guarded_history(tk, HISTORY_DAYS)
        except Exception:  # noqa: BLE001
            logger.warning("backtest: no history for %s", tk, exc_info=False)
            rows = []
        if len(rows) < 3:
            missing.append(tk)
        else:
            series[tk] = rows
    return series, missing


def measure(spec: dict, *, benchmark_ticker: str | None = None) -> dict:
    """Run one strategy end to end: fetch, backtest, and probe its fragility."""
    bench_tk = benchmark_ticker or get_settings().benchmark_ticker
    needed = bt.tickers_needed(spec)
    series, missing = _fetch(sorted(set(needed) | {bench_tk}))
    if missing:
        return {"ok": False, "reason": bt.MISSING_TICKER,
                "detail": f"no price history for {', '.join(missing)}"}
    bench = series.pop(bench_tk, None) if bench_tk not in needed else series.get(bench_tk)
    result = bt.run(series, spec, benchmark=bench)
    if not result.get("ok"):
        return result

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
    result["robustness"] = robustness
    return result


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
        row.data_source = market_provider().name
        row.period_start = str(result.get("start") or "")[:10]
        row.period_end = str(result.get("end") or "")[:10]
    if ok:
        row.observations = int(result.get("observations") or 0)
        row.computed_at = datetime.now(timezone.utc)
    return row


def _is_stale(row: StrategyBacktest) -> bool:
    if row.engine_version != ENGINE_VERSION:
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
    await session.commit()
    # Every strategy failing on price history at once is one provider outage,
    # not seven broken strategies -- and the resilience tier's circuit breaker
    # makes that the normal shape of a bad minute, since it opens for the whole
    # "history" tier at once. Saying so stops a run that should be retried from
    # reading as a catalog that fell apart.
    outage = bool(specs) and done == 0 and failed == len(specs)
    return {"computed": done, "abstained": failed, "engine_version": ENGINE_VERSION,
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
