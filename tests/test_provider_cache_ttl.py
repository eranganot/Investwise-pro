"""Slow-moving provider data must not be refetched at quote cadence.

Measured live: GET /api/v1/recommendations took 24.2s while every other endpoint
answered in under a second. Cause: quotes, 200-day history and fundamentals all
shared one 15-second TTL, so a 6-holding book refetched ~18 provider payloads on
every request (fundamentals twice per ticker -- once for the holding verdict,
once for the sector hedge -- plus 200 days of bars each).

15s is right for a quote and wrong for a quarterly filing.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.core.config import get_settings  # noqa: E402
from app.providers import registry  # noqa: E402


def test_slow_moving_data_gets_its_own_longer_cache():
    tiers = registry._tiers()
    s = get_settings()
    assert tiers["history"].cache.ttl == s.provider_history_cache_ttl_sec
    assert tiers["fundamentals"].cache.ttl == s.provider_fundamentals_cache_ttl_sec
    # Quotes keep the short TTL -- a stale price is a correctness bug.
    assert tiers["market"].cache.ttl == s.provider_cache_ttl_sec
    assert tiers["history"].cache.ttl > tiers["market"].cache.ttl
    assert tiers["fundamentals"].cache.ttl > tiers["history"].cache.ttl


def test_repeated_fundamentals_calls_hit_the_cache(monkeypatch):
    """The second lookup for a ticker must not touch the network.

    build_recommendations asks for the same ticker's fundamentals twice in one
    request; under the old shared TTL both could miss on a slow request.
    """
    calls = {"n": 0}

    class _Provider:
        name = "fake"

        def get_fundamentals(self, ticker):
            calls["n"] += 1
            return {"ticker": ticker}

        def get_history(self, ticker, days):
            calls["n"] += 1
            return [(1, 100.0)]

    registry._tiers.cache_clear()
    registry.market_provider.cache_clear()
    monkeypatch.setattr(registry, "market_provider", lambda: _Provider())

    registry.guarded_fundamentals("AMZN")
    registry.guarded_fundamentals("AMZN")   # holding verdict + sector hedge
    assert calls["n"] == 1, "second fundamentals lookup should be served from cache"

    registry.guarded_history("AMZN", days=200)
    registry.guarded_history("AMZN", days=200)
    assert calls["n"] == 2, "second history lookup should be served from cache"

    registry._tiers.cache_clear()
