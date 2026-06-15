"""Hourly market-data refresh state.

The Research feed updates on a fixed cadence (default hourly). With the built-in
deterministic provider the values are stable; when a live vendor is configured
this re-pulls fresh quotes/events each hour. We track when the last refresh ran
so the UI can show 'updated hourly / last refreshed N min ago'.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("investwise.market")

REFRESH_INTERVAL_MINUTES = 60
# Seed with process-start so status() always has a sensible time even before the
# first scheduled tick (or when the scheduler is disabled).
_last_refreshed: datetime = datetime.now(timezone.utc)
_refresh_count = 0


def refresh_market_data() -> dict:
    """Re-scan research events and stamp the refresh time. Safe to call hourly."""
    global _last_refreshed, _refresh_count
    try:
        from app.agents.research_agent import ResearchAgent
        ResearchAgent().scan()
    except Exception as exc:  # noqa: BLE001
        logger.warning("market refresh scan failed: %s", exc)
    _last_refreshed = datetime.now(timezone.utc)
    _refresh_count += 1
    logger.info("market data refreshed (#%d) at %s", _refresh_count, _last_refreshed.isoformat())
    return status()


def status() -> dict:
    return {
        "last_refreshed": _last_refreshed.isoformat(),
        "refresh_interval_minutes": REFRESH_INTERVAL_MINUTES,
        "refresh_count": _refresh_count,
    }
