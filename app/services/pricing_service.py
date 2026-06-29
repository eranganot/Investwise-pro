"""Scheduled price refresh for ALL holdings.

The hourly market refresh only rescans news; it never repriced positions, so
``current_price`` went stale and every price-based recommendation (tax-loss
harvest, concentration, drift) stayed silent even as holdings moved. This job
reprices every position from the live provider (primary -> Yahoo fallback) and
records the source actually used, so the data-status banner can tell the truth.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.tables import KVSetting, Position
from app.providers.live import YahooMarketDataProvider
from app.providers.registry import guarded_quote, market_provider

logger = logging.getLogger("investwise.pricing")

KV_LAST_SOURCE = "last_price_source"
KV_LAST_REFRESH = "last_price_refresh"


async def _kv_set(session, key: str, value: str) -> None:
    row = await session.get(KVSetting, key)
    if row:
        row.value = value
    else:
        session.add(KVSetting(key=key, value=value))


async def refresh_all_positions(session) -> dict:
    """Reprice every position. Returns {updated, failed, by_source}."""
    primary = market_provider()
    yahoo = None if primary.name == "yahoo" else YahooMarketDataProvider()
    positions = list((await session.scalars(select(Position))).all())
    by_source: dict[str, int] = {}
    updated = failed = 0
    quote_cache: dict[str, tuple] = {}  # ticker -> (price, source) or (None, None)

    for p in positions:
        tk = p.ticker
        if tk not in quote_cache:
            q, used = None, None
            try:
                q = guarded_quote(tk)
                used = primary.name
            except Exception:  # noqa: BLE001
                q = None
            if q is None and yahoo is not None:
                try:
                    q = yahoo.get_quote(tk)
                    used = yahoo.name
                except Exception:  # noqa: BLE001
                    q = None
            quote_cache[tk] = (q, used)
        q, used = quote_cache[tk]
        if q is None:
            failed += 1
            continue
        p.current_price = Decimal(str(q.price))
        p.meta = {**(p.meta or {}), "price_as_of": q.as_of, "price_source": used,
                  "price_currency": q.currency}
        by_source[used] = by_source.get(used, 0) + 1
        updated += 1

    if by_source:
        dominant = max(by_source, key=by_source.get)
        await _kv_set(session, KV_LAST_SOURCE, dominant)
    await _kv_set(session, KV_LAST_REFRESH, datetime.now(timezone.utc).isoformat())
    await session.commit()
    return {"updated": updated, "failed": failed, "by_source": by_source}


async def _refresh_all() -> dict:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as session:
            res = await refresh_all_positions(session)
            # With fresh prices, evaluate trading rules and fire alerts.
            try:
                from app.services.rules_service import evaluate_all
                res["rules"] = await evaluate_all(session)
            except Exception:  # noqa: BLE001
                logger.warning("rule evaluation failed", exc_info=False)
            return res
    finally:
        await engine.dispose()


def run_price_refresh_blocking() -> dict:
    """Sync entrypoint for APScheduler (runs in its own thread)."""
    try:
        res = asyncio.run(_refresh_all())
        logger.info("price refresh: %s", res)
        return res
    except Exception:  # noqa: BLE001
        logger.warning("scheduled price refresh failed", exc_info=True)
        return {"updated": 0, "failed": 0}


async def last_source(session) -> str | None:
    row = await session.get(KVSetting, KV_LAST_SOURCE)
    return row.value if row else None
