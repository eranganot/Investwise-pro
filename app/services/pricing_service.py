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
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.tables import KVSetting, Position, money
from app.providers.live import YahooMarketDataProvider
from app.providers.registry import guarded_quote, market_provider
from app.services.fx import major_unit, to_major_units
from app.services.intake_service import is_cash_position, repair_cash_row

logger = logging.getLogger("investwise.pricing")

KV_LAST_SOURCE = "last_price_source"
KV_LAST_REFRESH = "last_price_refresh"

# How long a quote may go without a new trade before we stop treating it as a
# current price. Counted in TRADING days, so a weekend or a public holiday can
# never flag a healthy holding -- the whole point is to catch instruments with no
# market left, not markets that happen to be shut.
STALE_AFTER_TRADING_DAYS = 5


def _parse_as_of(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def trading_days_between(earlier: datetime, later: datetime) -> int:
    """Whole Mon-Fri sessions between two instants (0 across a weekend)."""
    if later <= earlier:
        return 0
    d0, d1 = earlier.date(), later.date()
    days = 0
    cur = d0
    while cur < d1:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def quote_freshness(quote, now: datetime | None = None) -> tuple[str, str | None, int | None]:
    """(state, as_of_iso, trading_days_old) for one quote.

    Three states, deliberately -- collapsing them is the original bug:

    * ``fresh``   -- the venue reported a last-trade time inside the window.
    * ``stale``   -- the venue reported one, and it is older than the window.
      Nothing has traded; the price on file describes a market that has moved on
      (or, for a delisted instrument, one that no longer exists).
    * ``unknown`` -- the provider gave us no market timestamp at all, so its
      ``as_of`` is only "when we asked". We cannot judge freshness and must not
      pretend either way.
    """
    now = now or datetime.now(timezone.utc)
    if str(getattr(quote, "as_of_source", "request")) != "market":
        return ("unknown", None, None)
    as_of = _parse_as_of(getattr(quote, "as_of", None))
    if as_of is None:
        return ("unknown", None, None)
    aged = trading_days_between(as_of, now)
    state = "stale" if aged > STALE_AFTER_TRADING_DAYS else "fresh"
    return (state, as_of.isoformat(), aged)


async def _kv_set(session, key: str, value: str) -> None:
    row = await session.get(KVSetting, key)
    if row:
        row.value = value
    else:
        session.add(KVSetting(key=key, value=value))


async def refresh_all_positions(session, positions=None) -> dict:
    """Reprice positions, and judge whether each quote is actually current.

    ``positions`` defaults to every position in the database (the scheduled job).
    A caller that has already scoped rows to one user passes them in, so the
    manual refresh endpoint shares THIS implementation instead of keeping its own
    copy. It used to keep its own, and that copy had neither the cash guard nor
    the freshness check -- so a manual refresh happily repriced the synthetic
    CASH row as Pathward Financial and wrote a delisted holding's frozen price
    straight back in. One reprice path, or the fixes only apply to whichever one
    you happen to call.

    Returns {updated, failed, skipped_cash, repaired_cash, stale, by_source,
    stale_tickers, prices, errors}.

    A quote that has not traded inside the freshness window is **not** written as
    a current price. The position keeps the number it already had and is flagged
    ``price_stale`` with the date of the last real trade, so NAV consumers can
    say which part of the total they do not trust. It is not written off: the app
    must not decide a holding is worthless.
    """
    primary = market_provider()
    yahoo = None if primary.name == "yahoo" else YahooMarketDataProvider()
    if positions is None:
        positions = list((await session.scalars(select(Position))).all())
    by_source: dict[str, int] = {}
    updated = failed = skipped = repaired = stale = 0
    unknown_from_primary = 0
    minor_units = 0          # quotes that arrived in agorot / pence
    repaired_minor = 0       # rows a previous refresh stored in agorot / pence
    stale_tickers: list[dict] = []
    prices: list[dict] = []
    errors: list[dict] = []
    quote_cache: dict[str, tuple] = {}  # ticker -> (price, source) or (None, None)
    now = datetime.now(timezone.utc)

    for p in positions:
        tk = p.ticker
        # Cash is ILS-native: 1 unit = ₪1, never quoted. "CASH" is also a real
        # NASDAQ ticker (Pathward Financial, ~$73), so quoting it repriced the
        # balance as a US bank stock *and* stamped price_currency USD -- turning
        # ₪1,934.52 of cash into ₪521,904 of NAV once FX was applied. Skip it,
        # and heal any row a previous refresh already corrupted.
        if is_cash_position(tk, p.meta if isinstance(p.meta, dict) else None):
            skipped += 1
            if repair_cash_row(p):
                repaired += 1
            continue

        # Heal a row a PREVIOUS refresh stamped in agorot or pence, before doing
        # anything else with it. Deliberately not left to the fresh quote below:
        # a ticker whose quote fails, or whose quote is stale, keeps the price it
        # already has -- so on exactly the runs where the row is least likely to
        # be rewritten, the 100x value would survive another cycle. Same shape as
        # `repair_cash_row`, and the same reason: the corrupted rows are already
        # written and nothing else goes back for them.
        _pm = p.meta if isinstance(p.meta, dict) else {}
        _stamped = str(_pm.get("price_currency") or "")
        if _stamped:
            _major, _per = major_unit(_stamped)
            if _per != 1.0:
                _before = float(p.current_price or 0)
                p.current_price = money(_before / _per)
                p.meta = {**_pm, "price_currency": _major,
                          "price_currency_quoted": _stamped,
                          "price_currency_repaired": True}
                repaired_minor += 1
                logger.warning("repriced %s from %s %.4f to %s %.4f - it was "
                               "stored in a minor unit and valued at %gx",
                               tk, _stamped, _before, _major, _before / _per, _per)

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
            errors.append({"ticker": tk, "error": "no quote from any provider"})
            continue

        state, as_of, aged = quote_freshness(q, now)
        # The primary may not report a last-trade time at all (a provider that
        # stamps "now" on every quote can never look stale). Cross-check against
        # Yahoo, which does -- once per ticker, alongside the fallback we already
        # hold. Without this the whole check silently never fires in production.
        if state == "unknown":
            # The primary gave no venue timestamp. Say so once per run rather
            # than silently leaning on the cross-check: if this fires for every
            # ticker, the primary cannot support a freshness check at all and
            # the whole guard rests on Yahoo being reachable.
            unknown_from_primary += 1
        if state == "unknown" and yahoo is not None:
            key = f"__asof__{tk}"
            if key not in quote_cache:
                try:
                    quote_cache[key] = (yahoo.get_quote(tk), yahoo.name)
                except Exception:  # noqa: BLE001
                    quote_cache[key] = (None, None)
            yq, _ = quote_cache[key]
            if yq is not None:
                state, as_of, aged = quote_freshness(yq, now)

        # THE BOUNDARY. Everything downstream reads `current_price` alongside
        # `price_currency` and trusts the pair. Tel Aviv quotes AGOROT and calls
        # them "ILA"; London quotes PENCE and calls them "GBp". Verified live on
        # 2026-08-29: TEVA.TA came back ILA 11030.0, which is 110.30 ILS.
        #
        # Stamping the quote verbatim put a 100x price and a currency code no FX
        # provider knows into the row. The rate lookup then resolved to 1.0 --
        # once silently (the builtin provider has no ILA pair and returns 1.0
        # without erroring) and once through the fail-safe (the live provider
        # raises). Either way the holding was valued at a hundred times its
        # worth, in NAV and in everything sized against NAV.
        #
        # Normalised here, once, so that no consumer has to know which exchanges
        # do this. `to_major_units` is the only place the list lives.
        q_price, q_ccy = to_major_units(q.price, q.currency)
        quoted_ccy = (q.currency or "").strip()
        meta = {**(p.meta or {}), "price_source": used, "price_currency": q_ccy}
        if q_ccy != quoted_ccy:
            # Keep what the provider actually said. A normalisation that erases
            # its own input cannot be audited afterwards.
            meta["price_currency_quoted"] = quoted_ccy
            minor_units += 1
        meta["price_as_of"] = as_of or q.as_of
        # Say which of the three states we are in rather than leaving the reader
        # to infer it from an absent key.
        meta["price_freshness"] = state
        if state == "stale":
            # Do NOT overwrite the price: a quote nothing has traded against is
            # not a current price, and writing it makes the 30-minute refresh
            # "succeed" forever on a dead instrument.
            meta["price_stale"] = True
            meta["price_stale_days"] = aged
            p.meta = meta
            stale += 1
            stale_tickers.append({"ticker": tk, "as_of": as_of, "trading_days": aged})
            prices.append({"ticker": tk, "price": float(p.current_price or 0),
                           "currency": q_ccy, "as_of": as_of, "source": used,
                           "stale": True, "not_written": True})
            continue
        meta.pop("price_stale", None)
        meta.pop("price_stale_days", None)
        p.current_price = money(q_price)
        p.meta = meta
        by_source[used] = by_source.get(used, 0) + 1
        updated += 1
        prices.append({"ticker": tk, "price": q_price, "currency": q_ccy,
                       "as_of": meta["price_as_of"], "source": used,
                       "stale": False, "freshness": state})

    if by_source:
        dominant = max(by_source, key=by_source.get)
        await _kv_set(session, KV_LAST_SOURCE, dominant)
    await _kv_set(session, KV_LAST_REFRESH, datetime.now(timezone.utc).isoformat())
    await session.commit()
    if repaired:
        logger.warning("repaired %d cash row(s) mispriced by an earlier refresh", repaired)
    if stale_tickers:
        logger.warning("%d holding(s) have not traded inside the freshness window: %s",
                       stale, ", ".join(f"{x['ticker']}@{x['as_of']}" for x in stale_tickers))
    if unknown_from_primary and updated:
        logger.warning(
            "%s supplied no venue timestamp for %d quote(s); freshness fell back to "
            "the Yahoo cross-check. If this is every ticker, the primary cannot "
            "support a staleness check on its own.", primary.name, unknown_from_primary)
    if repaired_minor:
        logger.warning("price refresh: repaired %d position(s) that were stored "
                       "in a minor unit and therefore valued 100x too high",
                       repaired_minor)
    if minor_units:
        logger.info("price refresh: %d quote(s) arrived in a minor unit "
                    "(agorot / pence) and were normalised to the major unit",
                    minor_units)
    return {"updated": updated, "failed": failed, "skipped_cash": skipped,
            "unknown_from_primary": unknown_from_primary,
            "repaired_cash": repaired, "stale": stale,
            "minor_unit_quotes": minor_units,
            "repaired_minor_unit_rows": repaired_minor,
            "stale_tickers": stale_tickers, "by_source": by_source,
            "prices": prices, "errors": errors}


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
    """Sync entrypoint for APScheduler. MUST run on a thread with no event loop.

    The old body swallowed every exception and returned ``{"updated": 0,
    "failed": 0}``. That is not a failure value -- it is the exact shape of a
    successful run that found nothing to reprice, so a crash and a quiet no-op
    were the same two numbers to every reader downstream. Production logged the
    crash on EVERY boot and the job state said nothing was wrong, which is the
    ``nav_snapshot`` failure PR 2 removed one layer up: a job that reports
    success while doing nothing is worse than one that fails, because nobody
    goes looking.

    Exceptions now PROPAGATE to ``scheduler._guarded``, which is the one place
    that records an outcome. Two recorders is two versions of whether the job
    ran.
    """
    # A named failure beats asyncio's "cannot be called from a running event
    # loop", which reads like a library bug rather than what it is: a caller
    # putting this on the wrong thread.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass                       # no loop on this thread, which is required
    else:
        raise RuntimeError(
            "run_price_refresh_blocking() was called on a thread that already "
            "has a running event loop. It calls asyncio.run(), so it must run "
            "in a worker thread -- schedule it through scheduler._guarded "
            "rather than calling it from the FastAPI startup hook.")
    res = asyncio.run(_refresh_all())
    logger.info("price refresh: %s", res)
    return res


async def last_source(session) -> str | None:
    row = await session.get(KVSetting, KV_LAST_SOURCE)
    return row.value if row else None
