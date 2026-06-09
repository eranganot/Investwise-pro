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
    _scheduler.start()
    logger.info("APScheduler started (market data refresh every %d min).", REFRESH_INTERVAL_MINUTES)


def shutdown_scheduler() -> None:
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
