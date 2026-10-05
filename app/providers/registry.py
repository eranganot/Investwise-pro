"""Provider registry - selects the concrete adapter per config and wraps every
call in a per-provider resilience tier (cache -> rate limit -> breaker -> retry).
"""
from __future__ import annotations

import logging
from functools import lru_cache

from app.core.config import get_settings
from app.providers.base import (
    BrokerProvider, EconomicDataProvider, FXProvider, MarketDataProvider,
)
from app.providers.builtin import (
    BuiltinBrokerProvider, BuiltinEconomicDataProvider, BuiltinFXProvider,
    BuiltinMarketDataProvider,
)
from app.providers.live import FMPMarketDataProvider, FrankfurterFXProvider, YahooMarketDataProvider
from app.providers.resilience import CircuitBreaker, ResilienceTier, TokenBucket, TTLCache

_MARKET = {"builtin": BuiltinMarketDataProvider, "yahoo": YahooMarketDataProvider,
           "fmp": FMPMarketDataProvider}
_FX = {"builtin": BuiltinFXProvider, "frankfurter": FrankfurterFXProvider}


def _tier(cache_ttl_sec: float | None = None, stale_ttl_sec: float = 0.0) -> ResilienceTier:
    s = get_settings()
    return ResilienceTier(
        breaker=CircuitBreaker(s.provider_cb_failure_threshold, s.provider_cb_recovery_sec),
        bucket=TokenBucket(s.provider_rate_limit_per_sec, s.provider_rate_limit_per_sec),
        cache=TTLCache(s.provider_cache_ttl_sec if cache_ttl_sec is None else cache_ttl_sec,
                       stale_ttl=stale_ttl_sec),
        failure_cooloff=s.provider_failure_cooloff_sec,
    )


@lru_cache
def market_provider() -> MarketDataProvider:
    return _MARKET.get(get_settings().market_data_provider, BuiltinMarketDataProvider)()


@lru_cache
def fx_provider() -> FXProvider:
    """The configured FX provider, saying so when the rates are synthetic.

    `fx_provider` defaults to "builtin", whose rates are a frozen table --
    3.71 USD/ILS against a market that was ~2.98 on 2026-08-28. A deployment
    that does not set FX_PROVIDER therefore values every foreign holding about
    24% high, in NAV and in everything sized against NAV, with nothing on screen
    to say so. Once per process, out loud.
    """
    chosen = get_settings().fx_provider
    impl = _FX.get(chosen, BuiltinFXProvider)
    if impl is BuiltinFXProvider:
        logging.getLogger("investwise.providers").warning(
            "FX provider is %r -> BuiltinFXProvider, whose rates are a FROZEN "
            "TABLE, not market data (USD/ILS hardcoded at 3.71). Every non-ILS "
            "holding is being converted at that number. Set FX_PROVIDER="
            "frankfurter for real rates.", chosen)
    return impl()


@lru_cache
def economic_provider() -> EconomicDataProvider:
    """The economic-event source, saying out loud that there isn't one.

    This has never had an alternative -- no setting selects it, no other class
    implements `EconomicDataProvider`. Until PR 58 it returned four invented
    events that `market_impact` then annotated with the user's real holdings
    and real exposure, under the price-refresh clock. It now returns none, and
    the absence is announced the same way `fx_provider` announces the frozen
    table: once per process, in the logs, rather than left to be inferred from
    an empty card.
    """
    logging.getLogger("investwise.providers").warning(
        "No economic-data provider is configured -> BuiltinEconomicDataProvider, "
        "which returns NO events. 'What's moving' will be empty and will say so. "
        "It previously returned four hardcoded events; see STATUS.md PR 58.")
    return BuiltinEconomicDataProvider()


@lru_cache
def broker_provider() -> BrokerProvider:
    return BuiltinBrokerProvider()


@lru_cache
def _tiers() -> dict:
    s = get_settings()
    return {"market": _tier(), "fx": _tier(), "economic": _tier(),
            # Same resilience, longer memory: slow-moving data should not be
            # refetched at quote cadence. These two also keep an expired copy as
            # emergency stock -- daily bars and quarterly filings do not change
            # between fetches, so a stale one is the same answer. `market` and
            # `fx` deliberately do NOT: a stale PRICE misvalues the book.
            "history": _tier(s.provider_history_cache_ttl_sec,
                             s.provider_history_stale_ttl_sec),
            "fundamentals": _tier(s.provider_fundamentals_cache_ttl_sec,
                                  s.provider_fundamentals_stale_ttl_sec)}


# The provider's own range ladder, mirrored. `get_history(tk, days)` maps days
# onto one of Yahoo's ranges, so 130, 200 and 252 days all produce the SAME
# upstream URL -- but the cache key was `hist:{ticker}:{days}`, so they were
# three separate entries and three separate fetches of identical data. Across
# eight call sites asking for 130/200/252/2600, one ticker cost up to four
# requests that shared nothing.
#
# Keying on the bucket instead means the cache key changes exactly when the
# upstream request changes, which is the only thing it was ever supposed to
# track. On a 25-name scan that is the difference between 25 requests and 100.
_HISTORY_BUCKETS = (260, 520, 1300, 2600)


def _history_bucket(days: int) -> int:
    for b in _HISTORY_BUCKETS:
        if days <= b:
            return b
    return _HISTORY_BUCKETS[-1]


def guarded_quote(ticker: str):
    return _tiers()["market"].call(f"quote:{ticker}", lambda: market_provider().get_quote(ticker))


def guarded_history(ticker: str, days: int = 252):
    """One fetch per (ticker, upstream range), sliced to what the caller asked.

    A longer series already in cache answers a shorter request for free -- a
    backtest that pulled ten years a minute ago means the 200-day trend costs
    nothing rather than another round trip.
    """
    tier = _tiers()["history"]
    want = _history_bucket(days)
    for b in _HISTORY_BUCKETS:
        if b < want:
            continue
        cached = tier.cached(f"hist:{ticker}:{b}")
        if cached is not None:
            return cached[-days:]
    series = tier.call(f"hist:{ticker}:{want}",
                       lambda: market_provider().get_history(ticker, want))
    return series[-days:]


# What `guarded_total_return_history` says about the series it returned.
TOTAL_RETURN = "total_return"
PRICE_ONLY = "price_only"


def guarded_total_return_history(ticker: str, days: int = 252) -> tuple[list, str]:
    """Dividend-adjusted daily closes for a backtest, and which basis they are.

    PR 101: every backtest read plain closes, so a fund's dividends were
    invisible and a bond fund measured as losing money (BND -1.76%/yr over ten
    years; +1.25% with dividends), which made "sell all of BND" beat holding.
    Charts, stops and trend rules keep `guarded_history`: they trigger on the
    price you actually trade at.

    When the provider has no adjusted series (FMP, the builtin provider, or a
    Yahoo answer without `adjclose`) this falls back to plain closes and returns
    `PRICE_ONLY`, so the measurement can carry the label. Rate limits and an
    open circuit still raise: those are about us, not the ticker.
    """
    tier = _tiers()["history"]
    want = _history_bucket(days)
    series = None
    for b in _HISTORY_BUCKETS:
        if b < want:
            continue
        cached = tier.cached(f"histtr:{ticker}:{b}")
        if cached is not None:
            series = cached
            break
    if series is None:
        series = tier.call(f"histtr:{ticker}:{want}",
                           lambda: market_provider().get_total_return_history(ticker, want))
    if series and len(series) >= 2:
        return series[-days:], TOTAL_RETURN
    return guarded_history(ticker, days), PRICE_ONLY


def guarded_intraday(ticker: str, span: str = "1d"):
    """Intraday bars through the MARKET tier: quote cadence, no stale stock.
    The history tier keeps bars for hours, which is right for daily closes and
    wrong for a "today" chart."""
    return _tiers()["market"].call(f"intraday:{ticker}:{span}",
                                    lambda: market_provider().get_intraday(ticker, span))


def guarded_fundamentals(ticker: str):
    return _tiers()["fundamentals"].call(f"fund:{ticker}", lambda: market_provider().get_fundamentals(ticker))


def guarded_fx(base: str, quote: str):
    return _tiers()["fx"].call(f"fx:{base}/{quote}", lambda: fx_provider().get_rate(base, quote))


def guarded_events():
    return _tiers()["economic"].call("events", lambda: economic_provider().get_events())


def provider_health() -> dict:
    """State AND cause.

    This returned `{"circuit_state": ...}` and nothing else. So `history` could
    sit OPEN for a week and the endpoint that exists to explain provider health
    could not say which key failed, what the error was, when it last worked, or
    whether anything was succeeding alongside it -- which is the difference
    between diagnosing an outage and inferring one from source code.
    `circuit_state` is kept at the top level because the smoke scripts read it.
    """
    out: dict = {}
    for name, tier in _tiers().items():
        if tier.breaker is None:
            out[name] = {"circuit_state": "n/a"}
            continue
        snap = tier.breaker.snapshot()
        snap["stale_served"] = tier.stale_served
        out[name] = snap
    out["market_data_provider"] = market_provider().name
    out["fx_provider"] = fx_provider().name
    return out
