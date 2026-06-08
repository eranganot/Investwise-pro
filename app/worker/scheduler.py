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
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(lambda: logger.info("heartbeat: scheduled metric build tick"),
                       "interval", minutes=60, id="heartbeat")
    _scheduler.start()
    logger.info("APScheduler started (heartbeat hourly).")


def shutdown_scheduler() -> None:
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
