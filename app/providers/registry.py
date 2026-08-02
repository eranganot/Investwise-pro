"""Provider registry - selects the concrete adapter per config and wraps every
call in a per-provider resilience tier (cache -> rate limit -> breaker -> retry).
"""
from __future__ import annotations

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


def _tier(cache_ttl_sec: float | None = None) -> ResilienceTier:
    s = get_settings()
    return ResilienceTier(
        breaker=CircuitBreaker(s.provider_cb_failure_threshold, s.provider_cb_recovery_sec),
        bucket=TokenBucket(s.provider_rate_limit_per_sec, s.provider_rate_limit_per_sec),
        cache=TTLCache(s.provider_cache_ttl_sec if cache_ttl_sec is None else cache_ttl_sec),
    )


@lru_cache
def market_provider() -> MarketDataProvider:
    return _MARKET.get(get_settings().market_data_provider, BuiltinMarketDataProvider)()


@lru_cache
def fx_provider() -> FXProvider:
    return _FX.get(get_settings().fx_provider, BuiltinFXProvider)()


@lru_cache
def economic_provider() -> EconomicDataProvider:
    return BuiltinEconomicDataProvider()


@lru_cache
def broker_provider() -> BrokerProvider:
    return BuiltinBrokerProvider()


@lru_cache
def _tiers() -> dict:
    s = get_settings()
    return {"market": _tier(), "fx": _tier(), "economic": _tier(),
            # Same resilience, longer memory: slow-moving data should not be
            # refetched at quote cadence.
            "history": _tier(s.provider_history_cache_ttl_sec),
            "fundamentals": _tier(s.provider_fundamentals_cache_ttl_sec)}


def guarded_quote(ticker: str):
    return _tiers()["market"].call(f"quote:{ticker}", lambda: market_provider().get_quote(ticker))


def guarded_history(ticker: str, days: int = 252):
    return _tiers()["history"].call(f"hist:{ticker}:{days}", lambda: market_provider().get_history(ticker, days))


def guarded_fundamentals(ticker: str):
    return _tiers()["fundamentals"].call(f"fund:{ticker}", lambda: market_provider().get_fundamentals(ticker))


def guarded_fx(base: str, quote: str):
    return _tiers()["fx"].call(f"fx:{base}/{quote}", lambda: fx_provider().get_rate(base, quote))


def guarded_events():
    return _tiers()["economic"].call("events", lambda: economic_provider().get_events())


def provider_health() -> dict:
    out = {}
    for name, tier in _tiers().items():
        out[name] = {"circuit_state": tier.breaker.state if tier.breaker else "n/a"}
    out["market_data_provider"] = market_provider().name
    out["fx_provider"] = fx_provider().name
    return out
