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
from app.core.offload import offload
from app.models.tables import KVSetting, Position, db_money
from app.providers.live import YahooMarketDataProvider
from app.providers.registry import guarded_quote, market_provider
from app.services.fx import MARKET_CCY, major_unit, to_major_units
from app.services.intake_service import is_cash_position, repair_cash_row


def fallback_provider():
    """The secondary quote source, behind a seam so it can be stubbed.

    THIS CALL REACHED THE INTERNET FROM INSIDE THE TEST SUITE, and it is the
    reason `test_a_row_already_stored_in_agorot_is_repaired` failed on five
    different machines with five different numbers.

    The line was `yahoo = None if primary.name == "yahoo" else
    YahooMarketDataProvider()` -- a REAL provider, constructed unconditionally
    whenever the primary is anything else (so always, under the builtin default
    the tests run on), and then called at `yahoo.get_quote(tk)` **outside**
    `guarded_quote`. That test patches `registry.guarded_quote` AND
    `pricing_service.guarded_quote` to raise, exactly as the house rule says,
    and it still could not stop this: nothing named `guarded_quote` is on this
    path at all.

    MEASURED. The test forges a TASE row at 11030 agorot and asserts the repair
    lands it at 11030.00 ILS. Observed instead:

        10800.00  10810.00  10820.00  10830.00  10850.00

    across two local machines and CI runs #187 and #188. TEVA.TA traded at
    10820 agorot that afternoon. Those are not five bugs and not flakiness --
    the suite was asserting against a live market price, and the "no quote
    available at all" branch the test exists to exercise was never reached
    because a quote WAS available, by a route the test could not see.

    It is not only a test problem. This bypasses the whole resilience tier the
    registry exists to provide -- rate limiting, the circuit breaker, the cache,
    and the minor-unit/currency guards that live in `guarded_quote`. A provider
    call that skips those is one the app cannot throttle, break or observe.
    Routing it through the tier is the better fix and is left as a named
    remaining item rather than smuggled into a bug-fix PR; this seam is the
    minimum that makes the path stubbable and the failure visible.
    """
    return YahooMarketDataProvider()


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
    yahoo = None if primary.name == "yahoo" else fallback_provider()
    if positions is None:
        positions = list((await session.scalars(select(Position))).all())
    by_source: dict[str, int] = {}
    updated = failed = skipped = repaired = stale = 0
    unknown_from_primary = 0
    minor_units = 0          # quotes that arrived in agorot / pence
    repaired_minor = 0       # rows a previous refresh stored in agorot / pence
    conflicts = 0            # quotes whose currency contradicts the declared market
    repaired_conflict = 0    # rows an earlier refresh already redenominated
    unpriced = 0             # quotes that came back at or below zero
    stale_tickers: list[dict] = []
    unpriced_tickers: list[str] = []
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
                p.current_price = db_money(_before / _per)
                p.meta = {**_pm, "price_currency": _major,
                          "price_currency_quoted": _stamped,
                          "price_currency_repaired": True}
                repaired_minor += 1
                logger.warning("repriced %s from %s %.4f to %s %.4f - it was "
                               "stored in a minor unit and valued at %gx",
                               tk, _stamped, _before, _major, _before / _per, _per)

        # And heal a row an EARLIER refresh redenominated. Refusing the quote
        # below stops new damage; it does nothing for the rows already carrying
        # a currency that contradicts their market, and nothing else goes back
        # for those. A TASE holding stamped USD is being multiplied by the FX
        # rate on every read.
        #
        # The currency is restored to the one the market implies. The PRICE is
        # not, and cannot be: it was written by a provider answering about a
        # different instrument, so there is no arithmetic that recovers the
        # right number from it. The row is flagged `price_unverified` instead of
        # being quietly trusted -- the app must say which part of the total it
        # does not stand behind rather than pick a number.
        _pm2 = p.meta if isinstance(p.meta, dict) else {}
        # getattr, not p.market: `market` is NOT NULL on Position, so every real
        # row has one -- but a refresh must not 500 the app because one row came
        # from somewhere odd. No market means no declaration to contradict, and
        # the quote is trusted, which is the same rule as an unknown market.
        _declared_now = MARKET_CCY.get(str(getattr(p, "market", "") or "").upper())
        _stamped_ccy = str(_pm2.get("price_currency") or "")
        if _declared_now and _stamped_ccy and _stamped_ccy != _declared_now:
            p.meta = {**_pm2, "price_currency": _declared_now,
                      "price_unverified": True,
                      "price_currency_conflict": {"declared": _declared_now,
                                                  "quoted": _stamped_ccy,
                                                  "market": getattr(p, "market", None),
                                                  "source": "repair"}}
            repaired_conflict += 1
            logger.warning(
                "%s is held on %s (%s) but was stored as %s - restoring %s. Its "
                "price was written by a provider answering about something else "
                "and is now flagged unverified.",
                tk, getattr(p, "market", "?"), _declared_now, _stamped_ccy,
                _declared_now)

        if tk not in quote_cache:
            q, used = None, None
            try:
                # This is the scheduled refresh, and it runs IN-PROCESS on the
                # same loop that serves requests: a refresh over N positions
                # used to hold the loop for N round trips, so the app went
                # unresponsive on a timer rather than under load. The Yahoo
                # fallback below is the same call by another name and gets the
                # same treatment.
                q = await offload(guarded_quote, tk)
                used = primary.name
            except Exception:  # noqa: BLE001
                q = None
            if q is None and yahoo is not None:
                try:
                    q = await offload(yahoo.get_quote, tk)
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

        # A QUOTE OF ZERO IS NOT A QUOTE, AND IT IS THE ONE THAT WAS GETTING IN.
        #
        # Every other way a quote can be unusable is already refused here and
        # said out loud: no quote at all is `failed`; a quote nothing has traded
        # against is `stale` and `not_written`; a quote in a currency that
        # contradicts the holding's market is a `conflict` and `not_written`.
        # The module's own comment states the rule -- "Do NOT overwrite the
        # price: a quote nothing has traded against is not a current price".
        # A price of 0 is the same class of thing and it walked straight through
        # to `p.current_price = db_money(q_price)` twenty lines below.
        #
        # What that costs, MEASURED on this book: Eran opened the app on
        # 2026-09-01 and found holdings showing 0 ILS, deleted two of them
        # because a holding worth nothing looks like junk, and by the next
        # morning the remaining rows were priced normally again -- because the
        # next 30-minute refresh got a real quote and healed it silently. The
        # deletion is the expensive part: a zeroed row is not a worthless
        # holding, and the app gave him no way to tell the difference.
        #
        # It is not only a display bug. `value_ils` is quantity x price x rate,
        # so a zeroed holding leaves NAV entirely, and NAV is what the mix, the
        # rebalance sizing, the concentration cap, the projection and the 22:10
        # snapshot are all measured against. A snapshot taken inside a zeroed
        # window is wrong permanently -- it is the one number the app cannot
        # recompute later.
        #
        # The provider adapters now refuse a non-positive price too, which is
        # the better place to stop it (the fallback provider gets its turn and
        # `guarded_quote` caches nothing). This is the belt: no quote source,
        # present or future, gets to write a holding down to zero from here.
        if q_price is None or float(q_price) <= 0:
            meta = {**(p.meta or {}), "price_source": used,
                    "price_freshness": "unpriced", "price_unpriced": True,
                    "price_unpriced_quote": {"price": (None if q.price is None
                                                       else float(q.price)),
                                             "currency": quoted_ccy,
                                             "source": used}}
            p.meta = meta
            unpriced += 1
            unpriced_tickers.append(tk)
            logger.warning(
                "%s quoted %r from %s - refusing it rather than writing the "
                "holding down to zero. Price left at %s.",
                tk, q.price, used, p.current_price)
            prices.append({"ticker": tk, "price": float(p.current_price or 0),
                           "currency": q_ccy, "as_of": (p.meta or {}).get("price_as_of"),
                           "source": used, "stale": False, "not_written": True,
                           "unpriced": True})
            continue

        # A QUOTE IN THE WRONG CURRENCY IS NOT A QUOTE ABOUT THIS HOLDING.
        #
        # The market on the position is the user's DECLARATION -- he chose
        # "Tel Aviv (TASE)" in the holdings form. The provider has its own
        # opinion, derived from the ticker: the builtin one is literally
        # `"TASE" if ticker.upper().startswith("TA") else "NYSE"`
        # (`builtin.py:37`), and the live ones answer for whatever venue they
        # think the symbol belongs to. Stamping the quote's currency let that
        # opinion OVERRULE the declaration.
        #
        # Measured, on the exact book in `test_cash.py`: two TASE holdings at
        # 100 ILS, one refresh, and the rows come back stamped USD --
        # NAV 41,000 -> 235,687 and invested 41,000 -> 152,110, both exactly
        # x3.71. That is CI run #169's `192360.0 <= 41000.0 + 1.0` and run
        # #178's identical failure: the cash figure of 148,953 reconstructs to
        # the shekel from 293 shares at the builtin's synthetic 149.37, times
        # 3.71, less 25% CGT on the gain.
        #
        # It was intermittent only because it needs a refresh to land inside
        # the test -- the scheduler primes one on startup. The arithmetic is
        # deterministic, and it is reachable in production by anyone holding a
        # Tel Aviv or London name under a ticker the provider reads as US.
        #
        # So: a disagreement means the provider is answering about something
        # else. Do not restamp the currency AND do not write the price -- the
        # same rule already applied to a stale quote, for the same reason.
        declared_ccy = MARKET_CCY.get(str(getattr(p, "market", "") or "").upper())
        if declared_ccy and q_ccy and q_ccy != declared_ccy:
            meta = {**(p.meta or {}),
                    "price_currency_conflict": {"declared": declared_ccy,
                                                "quoted": quoted_ccy or q_ccy,
                                                "market": getattr(p, "market", None),
                                                "source": used}}
            p.meta = meta
            conflicts += 1
            logger.warning(
                "%s is held on %s (%s) but %s quoted it in %s - refusing the "
                "quote rather than redenominating the holding. Price left at %s.",
                tk, getattr(p, "market", "?"), declared_ccy, used, quoted_ccy or q_ccy,
                p.current_price)
            prices.append({"ticker": tk, "price": float(p.current_price or 0),
                           "currency": declared_ccy, "as_of": (p.meta or {}).get("price_as_of"),
                           "source": used, "stale": False, "not_written": True,
                           "currency_conflict": {"declared": declared_ccy,
                                                 "quoted": quoted_ccy or q_ccy}})
            continue

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
        # A real price heals the row, including one an earlier refresh or an
        # older build had zeroed. Leaving the flag on a holding that is priced
        # again would be the mirror of the bug: a warning that outlives its
        # cause is one nobody reads.
        meta.pop("price_unpriced", None)
        meta.pop("price_unpriced_quote", None)
        p.current_price = db_money(q_price)
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
    if repaired_conflict:
        logger.error("price refresh: restored the declared currency on %d "
                     "position(s) an earlier refresh had redenominated. Those "
                     "rows were being multiplied by the FX rate on every read.",
                     repaired_conflict)
    if conflicts:
        logger.error("price refresh: %d quote(s) REFUSED because the provider's "
                     "currency contradicted the market the holding is declared "
                     "on. Those positions keep their existing price; the app is "
                     "not redenominating a holding on a provider's guess.",
                     conflicts)
    if repaired_minor:
        logger.warning("price refresh: repaired %d position(s) that were stored "
                       "in a minor unit and therefore valued 100x too high",
                       repaired_minor)
    if minor_units:
        logger.info("price refresh: %d quote(s) arrived in a minor unit "
                    "(agorot / pence) and were normalised to the major unit",
                    minor_units)
    if unpriced_tickers:
        logger.warning("price refresh: %d quote(s) came back at or below zero "
                       "and were refused rather than written: %s",
                       unpriced, ", ".join(unpriced_tickers))
    return {"updated": updated, "failed": failed, "skipped_cash": skipped,
            "unpriced": unpriced, "unpriced_tickers": unpriced_tickers,
            "unknown_from_primary": unknown_from_primary,
            "repaired_cash": repaired, "stale": stale,
            "minor_unit_quotes": minor_units,
            "repaired_minor_unit_rows": repaired_minor,
            "currency_conflicts": conflicts,
            "repaired_currency_rows": repaired_conflict,
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
