"""APScheduler cron routines (Section AD) - optional, gated by enable_scheduler.

Every job records its outcome in ``JOB_STATE`` so "nothing happened" is
diagnosable. Notifications went silent for two weeks with no way to tell whether
the scheduler had stopped, a job was wedged, or the pushes themselves were
failing -- because each failure path was a bare logger.warning and nothing
persisted. Two concrete hardening measures here:

  * **misfire_grace_time** -- APScheduler defaults to 1 second, so a job whose
    fire time passes while the process is restarting or the pool is busy is
    dropped silently. On a platform that redeploys, a 07:00 daily cron with a
    1-second grace is a coin flip; the digest could go weeks without running.
  * **execution timeouts** -- an interval job with ``max_instances=1`` that
    hangs (an HTTP call with no timeout) blocks every subsequent run *forever*.
    Jobs are wrapped so a hung run fails loudly instead of wedging the schedule.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("investwise.scheduler")
_scheduler = None

# job id -> {last_run, last_ok, last_error, last_result, runs, failures}
JOB_STATE: dict[str, dict] = {}
_STATE_LOCK = threading.Lock()

# A job that outlives this is treated as wedged rather than left to block the
# slot indefinitely.
JOB_TIMEOUT_SEC = 600


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(job_id: str, *, ok: bool, result=None, error: str | None = None) -> None:
    with _STATE_LOCK:
        st = JOB_STATE.setdefault(job_id, {"runs": 0, "failures": 0})
        st["runs"] += 1
        if st.get("last_run"):
            st["previous_run"] = st["last_run"]     # PR 143: shows history crossed a restart
        st["last_run"] = _now_iso()
        st["last_ok"] = ok
        if ok:
            st["last_result"] = result if isinstance(result, (dict, str, int)) else None
            st["last_success"] = st["last_run"]
        else:
            st["failures"] += 1
            st["last_error"] = (error or "")[:300]
            st["last_failure"] = st["last_run"]
        snap = dict(st)
    _persist(job_id, snap)


# PR 143 -- JOB HISTORY SURVIVES A DEPLOY. `JOB_STATE` was memory only: read on
# production 2026-10-23 11:05 UTC, four minutes after a deploy, every job showed
# `runs: 1` and the nightly ones no history at all, so whether the 06:15 signal job
# or the 22:10 snapshot ran could not be told from the app -- and the app deploys
# several times a day. Each outcome is now also written to `kv_settings`
# (`job_state:<id>`) and read back when the scheduler starts.
_KV_PREFIX = "job_state:"


def _off_loop(fn, timeout: float = 15.0):
    """Run the coroutine function `fn` with asyncio.run on a fresh thread: the
    callers include FastAPI's startup hook, whose thread already has a loop."""
    import asyncio
    box: dict = {}

    def _t():
        try:
            box["v"] = asyncio.run(fn())
        except Exception as exc:  # noqa: BLE001
            box["e"] = exc
    t = threading.Thread(target=_t, daemon=True, name="job-state")
    t.start()
    t.join(timeout)
    if "e" in box:
        raise box["e"]
    return box.get("v")


async def _with_session(work):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(eng, expire_on_commit=False)() as s:
            return await work(s)
    finally:
        await eng.dispose()


def _persist(job_id: str, st: dict) -> None:
    import json

    async def work(s):
        from app.models.tables import KVSetting
        key = (_KV_PREFIX + job_id)[:80]
        row = await s.get(KVSetting, key)
        val = json.dumps(st, default=str)
        if row is None:
            s.add(KVSetting(key=key, value=val))
        else:
            row.value = val
        await s.commit()
    try:
        _off_loop(lambda: _with_session(work))
    except Exception:  # noqa: BLE001 - recording must never fail the job; it says so in the log
        logger.warning("could not persist job state for %s", job_id, exc_info=True)


def load_persisted_state() -> int:
    """Read every persisted job outcome into JOB_STATE. Returns how many."""
    import json

    async def work(s):
        from sqlalchemy import select

        from app.models.tables import KVSetting
        return list((await s.execute(select(KVSetting).where(
            KVSetting.key.like(_KV_PREFIX + "%")))).scalars().all())
    try:
        rows = _off_loop(lambda: _with_session(work)) or []
    except Exception:  # noqa: BLE001
        logger.warning("could not read persisted job state", exc_info=True)
        return 0
    n = 0
    with _STATE_LOCK:
        for r in rows:
            try:
                st = json.loads(r.value)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(st, dict):
                JOB_STATE[r.key[len(_KV_PREFIX):]] = st
                n += 1
    return n


# PR 144: every job's entry point, so the boot self-check can resolve what each
# job imports only when it runs.
ENTRY_POINTS: dict = {}


def _guarded(job_id: str, fn):
    """Wrap a job so it always records an outcome and can never wedge silently."""
    ENTRY_POINTS[job_id] = fn

    def _run():
        box: dict = {}

        def _target():
            try:
                box["result"] = fn()
            except Exception as exc:  # noqa: BLE001
                box["error"] = f"{type(exc).__name__}: {exc}"

        t = threading.Thread(target=_target, daemon=True, name=f"job:{job_id}")
        t.start()
        t.join(JOB_TIMEOUT_SEC)
        if t.is_alive():
            # The worker thread is abandoned rather than joined forever: the next
            # scheduled run gets a clean slot instead of being skipped for good.
            _record(job_id, ok=False, error=f"timed out after {JOB_TIMEOUT_SEC}s")
            logger.error("scheduled job %s timed out after %ds", job_id, JOB_TIMEOUT_SEC)
            return
        if "error" in box:
            _record(job_id, ok=False, error=box["error"])
            logger.warning("scheduled job %s failed: %s", job_id, box["error"])
            return
        _record(job_id, ok=True, result=box.get("result"))
    return _run


def job_state() -> dict:
    """Snapshot of every job's last outcome (for the push/status endpoint)."""
    with _STATE_LOCK:
        state = {k: dict(v) for k, v in JOB_STATE.items()}
    running = bool(_scheduler and getattr(_scheduler, "running", False))
    jobs = []
    if running:
        for j in _scheduler.get_jobs():
            nxt = getattr(j, "next_run_time", None)
            jobs.append({"id": j.id, "next_run": nxt.isoformat() if nxt else None})
    return {"scheduler_running": running, "jobs": jobs, "history": state}


def start_scheduler() -> None:
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception:  # noqa: BLE001
        logger.warning("APScheduler not installed; scheduler disabled.")
        return
    load_persisted_state()          # PR 143: last night's outcomes, not an empty page
    # A generous default grace: better a late digest than a silently dropped one.
    _scheduler = BackgroundScheduler(
        daemon=True,
        job_defaults={"max_instances": 1, "coalesce": True, "misfire_grace_time": 3600},
    )
    # Reprice every holding so current_price never goes stale (drives the
    # price-based recommendations and the price-move notifications).
    try:
        # THE PRIME USED TO BE A BARE CALL ON THIS THREAD AND IT COULD NEVER WORK.
        #
        # `start_scheduler()` runs from FastAPI's startup hook, so this thread
        # already has a running event loop -- and `run_price_refresh_blocking`
        # calls `asyncio.run()`, which refuses to nest. Every boot logged
        # "scheduled price refresh failed ... asyncio.run() cannot be called from
        # a running event loop", then RETURNED {"updated": 0, "failed": 0}, which
        # is indistinguishable from a clean run that found nothing to do. Seen
        # live in the 2026-08-25 16:07:27 Railway log.
        #
        # Two things were wrong and both are fixed:
        #
        #   It was UNGUARDED, so the failure never reached JOB_STATE and
        #   `push_service.blockers` -- which PR 2 taught to read job state --
        #   could not name it. A job that fails outside `_guarded` is a job that
        #   fails invisibly.
        #
        #   It ran ON THE LOOP. `_guarded` already runs its target in a worker
        #   thread, which is the context `asyncio.run()` needs, so the scheduled
        #   30-minute runs were always fine; only the prime was broken.
        #
        # `next_run_time` primes it through the SAME guarded path instead of a
        # separate call: one registration, one recorder, and no second code path
        # that can rot. Calling `_guarded(...)()` here directly would have worked
        # too and would have blocked startup for up to JOB_TIMEOUT_SEC.
        from app.services.pricing_service import run_price_refresh_blocking
        _scheduler.add_job(_guarded("price_refresh", run_price_refresh_blocking),
                           "interval", minutes=30, id="price_refresh",
                           next_run_time=datetime.now(timezone.utc))
        logger.info("Position price refresh scheduled (every 30 min, priming now).")
    except Exception:  # noqa: BLE001
        logger.warning("Price refresh job not scheduled.", exc_info=False)

    # Warm the futures/regime cache so the macro signal is live for the agents.
    try:
        from app.services.markets_service import futures_snapshot

        # PR 107 (C4, L11): an outage RAISES (the job records it; the previous
        # snapshot is kept, marked stale), and the prime goes through the
        # scheduler instead of the startup thread, so boot no longer waits for
        # the slowest of eight Yahoo fetches (12 s stub -> 12.1 s boot).
        def _warm_futures():
            # PR 108: the strategies' regime first (hourly at most), so Today's
            # risk card has a warm cache; then the futures cross-check.
            from app.services.regime_service import refresh_regime_proxy
            refresh_regime_proxy()
            futures_snapshot(force=True, raise_on_outage=True)
        _scheduler.add_job(_guarded("futures_warm", _warm_futures), "interval",
                           minutes=5, id="futures_warm", next_run_time=datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001
        logger.warning("Futures warm job not scheduled.", exc_info=False)

    # Push notifications: scan portfolios for important changes, and a daily digest.
    try:
        from app.services.push_service import run_digests_blocking, run_evaluations_blocking
        # PR 156 (D140): every 30 min, 5 min behind the 30-min price refresh
        # (both start at boot), because that is when cards change. Hourly let a
        # card that lived under an hour come and go with no push.
        _scheduler.add_job(_guarded("push_evaluate", run_evaluations_blocking),
                           "interval", minutes=30, id="push_evaluate",
                           next_run_time=datetime.now(timezone.utc) + timedelta(minutes=5))
        # The digest is idempotent per calendar day (its dedupe signature is the
        # date), so a wide grace window can only make it late, never duplicate.
        _scheduler.add_job(_guarded("push_digest", run_digests_blocking), "cron",
                           hour=7, minute=0, id="push_digest", misfire_grace_time=21600)
        logger.info("Push notification jobs scheduled (evaluate every 30 min, digest 07:00).")
    except Exception:  # noqa: BLE001
        logger.warning("Push notification jobs not scheduled.", exc_info=False)

    # Strategy backtests: ten years of daily closes per ticker is far too much
    # network to do inside a page load, so the numbers are precomputed and the
    # route only ever reads stored rows. Nightly is ample -- one more session
    # cannot move a ten-year CAGR, and a stale row renders as stale anyway.
    # Phase N: one NAV row and one health row per user, daily. Runs at 22:10 UTC,
    # after the 30-minute price refresh has repriced the book and well after any
    # US close, so the value recorded is a settled one rather than mid-session.
    #
    # This is the only job whose output cannot be recomputed later. A missed
    # backtest is recomputed tonight; a missed day of NAV is gone permanently,
    # which is why its misfire grace is long.
    try:
        from app.services.nav_history import run_nav_snapshot_blocking
        _scheduler.add_job(_guarded("nav_snapshot", run_nav_snapshot_blocking),
                           "cron", hour=22, minute=10, id="nav_snapshot",
                           misfire_grace_time=43200)
        logger.info("NAV snapshot scheduled (22:10 daily).")
    except Exception:  # noqa: BLE001
        logger.warning("NAV snapshot job not scheduled.", exc_info=False)

    try:
        from app.services.backtest_service import run_backtest_refresh_blocking
        _scheduler.add_job(_guarded("backtest_refresh", run_backtest_refresh_blocking),
                           "cron", hour=3, minute=30, id="backtest_refresh",
                           misfire_grace_time=21600)
        logger.info("Strategy backtest refresh scheduled (03:30 daily).")
    except Exception:  # noqa: BLE001
        logger.warning("Strategy backtest refresh not scheduled.", exc_info=False)

    try:
        # PR 132 (D97): which strategies move together, for the same-bet test.
        # After the 03:30 backtest refresh, on the same price history.
        from app.services.sleeve_diversity import run_correlation_refresh_blocking
        _scheduler.add_job(_guarded("strategy_correlation", run_correlation_refresh_blocking),
                           "cron", hour=3, minute=50, id="strategy_correlation",
                           misfire_grace_time=21600)
        logger.info("Strategy correlation refresh scheduled (03:50 daily).")
    except Exception:  # noqa: BLE001
        logger.warning("Strategy correlation refresh not scheduled.", exc_info=False)

    try:
        # T5. 04:10, after the 03:30 backtest refresh, because a re-solve reads
        # the measurements that job writes -- solving first would answer from
        # yesterday's numbers and stamp today's timestamp on them.
        from app.services.solver_store import run_solver_refresh_blocking
        _scheduler.add_job(_guarded("solver_refresh", run_solver_refresh_blocking),
                           "cron", hour=4, minute=10, id="solver_refresh",
                           misfire_grace_time=21600)
        logger.info("Target solver refresh scheduled (04:10 daily).")
    except Exception:  # noqa: BLE001
        logger.warning("Backtest refresh job not scheduled.", exc_info=False)

    try:
        # PR 135a (wave 4): every sleeve move scored by the goal model, after the
        # 04:10 goal solve and on the same measurements; Today reads the result.
        from app.services.sleeve_moves import run_sleeve_moves_blocking
        _scheduler.add_job(_guarded("sleeve_moves", run_sleeve_moves_blocking),
                           "cron", hour=4, minute=40, id="sleeve_moves",
                           misfire_grace_time=21600)
        logger.info("Sleeve moves scheduled (04:40 daily).")
    except Exception:  # noqa: BLE001
        logger.warning("Sleeve moves job not scheduled.", exc_info=False)

    try:
        # PR 136a (wave 5): the best book the app can measure for each goal,
        # after the 04:40 sleeve moves and on the same measurements.
        from app.services.book_optimizer import run_book_optimizer_blocking
        _scheduler.add_job(_guarded("book_optimizer", run_book_optimizer_blocking),
                           "cron", hour=5, minute=10, id="book_optimizer",
                           misfire_grace_time=21600)
        logger.info("Book optimizer scheduled (05:10 daily).")
    except Exception:  # noqa: BLE001
        logger.warning("Book optimizer job not scheduled.", exc_info=False)

    try:
        # A3r (D132): one exit per holding, measured on the goal replay after the
        # 04:10 goal solve; Today's card reads the stored plan.
        from app.services.protection_manager import run_protection_manager_blocking
        _scheduler.add_job(_guarded("protection_manager", run_protection_manager_blocking),
                           "cron", hour=5, minute=40, id="protection_manager",
                           misfire_grace_time=21600)
        logger.info("Protection manager scheduled (05:40 daily).")
    except Exception:  # noqa: BLE001
        logger.warning("Protection manager job not scheduled.", exc_info=False)

    # PR 77: the holding review's backtests (each holding's own ~10 years, both
    # sell signals against holding). Nightly, after the strategy backtests, so the
    # history they fetched is cached; the Today build only reads the results.
    try:
        from app.services.holding_review import run_holding_review_blocking
        _scheduler.add_job(_guarded("holding_review", run_holding_review_blocking),
                           "cron", hour=4, minute=40, id="holding_review",
                           misfire_grace_time=21600)
        logger.info("Holding review measurement scheduled (04:40 daily).")
    except Exception:  # noqa: BLE001
        logger.warning("Holding review job not scheduled.", exc_info=False)

    # Evaluate the active rule-based strategy once a day, after the US close has
    # settled into the daily feed. Hourly would not help: these rules read daily
    # closes, so an intraday re-evaluation can only repeat itself or react to a
    # bar that is not final yet.
    try:
        from app.services.strategy_signal_service import run_strategy_signals_blocking
        _scheduler.add_job(_guarded("strategy_signals", run_strategy_signals_blocking),
                           "cron", hour=6, minute=15, id="strategy_signals",
                           misfire_grace_time=21600)
        logger.info("Strategy signal evaluation scheduled (06:15 daily).")
    except Exception:  # noqa: BLE001
        logger.warning("Strategy signal job not scheduled.", exc_info=False)

    # Standing orders: hourly, primed at boot. Hourly rather than one daily
    # cron because the job is idempotent (a run that is not due does nothing,
    # and a credited month is never credited twice) -- so the only effect of a
    # tighter schedule is that a deposit due at local midnight lands within the
    # hour, and a boot after downtime catches up at once instead of tomorrow.
    try:
        from app.services.standing_order_service import run_standing_orders_blocking
        _scheduler.add_job(_guarded("standing_orders", run_standing_orders_blocking),
                           "interval", minutes=60, id="standing_orders",
                           next_run_time=datetime.now(timezone.utc))
        logger.info("Standing orders scheduled (hourly, priming now).")
    except Exception:  # noqa: BLE001
        logger.warning("Standing order job not scheduled.", exc_info=False)

    run_boot_check()
    _scheduler.start()
    # PR 112: the hourly `market_refresh` job is gone. It ran ResearchAgent().scan()
    # and threw the result away, stamping a time nothing read (market_state.py).
    logger.info("APScheduler started (%d jobs).", len(_scheduler.get_jobs()))


def run_boot_check() -> dict:
    """PR 144: resolve every import each job makes only when it runs, and record
    the answer as the job `boot_check` (persisted, shown on /push/status and, when
    it fails, on Today). Never stops the app: a broken job is reported, not fatal."""
    # A job with no history yet is "registered" now, so a daily job that never
    # runs is visible after 26 h instead of looking like "no news" for ever.
    for jid in sorted(ENTRY_POINTS):
        with _STATE_LOCK:
            st = JOB_STATE.setdefault(jid, {"runs": 0, "failures": 0})
            fresh = "registered_at" not in st
            if fresh:
                st["registered_at"] = _now_iso()
            snap = dict(st)
        if fresh:
            _persist(jid, snap)
    try:
        from app.worker.self_check import check_jobs
        res = check_jobs(dict(ENTRY_POINTS))
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "errors": [f"the self-check itself failed: {type(exc).__name__}: {exc}"]}
    if res.get("ok"):
        _record("boot_check", ok=True, result={"jobs": sorted(ENTRY_POINTS),
                                               "modules": res.get("modules", [])})
        logger.info("boot check: %d job(s) resolve", len(ENTRY_POINTS))
    else:
        _record("boot_check", ok=False, error="; ".join(res.get("errors") or [])[:300])
        logger.error("boot check FAILED: %s", res.get("errors"))
    return res


def shutdown_scheduler() -> None:
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
