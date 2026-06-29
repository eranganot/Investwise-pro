"""APScheduler cron routines (Section AD) - optional, gated by enable_scheduler."""
from __future__ import annotations

import logging

logger = logging.getLogger("investwise.scheduler")
_scheduler = None


def start_scheduler() -> None:
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception:  # noqa: BLE001
        logger.warning("APScheduler not installed; scheduler disabled.")
        return
    from app.services.market_state import REFRESH_INTERVAL_MINUTES, refresh_market_data
    _scheduler = BackgroundScheduler(daemon=True)
    refresh_market_data()  # warm once at startup
    _scheduler.add_job(refresh_market_data, "interval",
                       minutes=REFRESH_INTERVAL_MINUTES, id="market_refresh")

    # Warm the futures/regime cache so the macro signal is live for the agents.
    try:
        from app.services.markets_service import futures_snapshot
        def _warm_futures():
            try:
                futures_snapshot(force=True)
            except Exception:  # noqa: BLE001
                pass
        _warm_futures()
        _scheduler.add_job(_warm_futures, "interval", minutes=5,
                           id="futures_warm", max_instances=1, coalesce=True)
    except Exception:  # noqa: BLE001
        logger.warning("Futures warm job not scheduled.", exc_info=False)

    # Push notifications: scan portfolios for important changes, and a daily digest.
    try:
        from app.services.push_service import run_digests_blocking, run_evaluations_blocking
        _scheduler.add_job(run_evaluations_blocking, "interval", minutes=60,
                           id="push_evaluate", max_instances=1, coalesce=True)
        _scheduler.add_job(run_digests_blocking, "cron", hour=7, minute=0,
                           id="push_digest", max_instances=1, coalesce=True)
        logger.info("Push notification jobs scheduled (evaluate hourly, digest 07:00).")
    except Exception:  # noqa: BLE001
        logger.warning("Push notification jobs not scheduled.", exc_info=False)

    _scheduler.start()
    logger.info("APScheduler started (market data refresh every %d min).", REFRESH_INTERVAL_MINUTES)


def shutdown_scheduler() -> None:
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
