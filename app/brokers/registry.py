"""Pick the aggregator adapter from config. Mock works outside production only
(PR 110); real providers require ``broker_enabled`` + their credentials."""
from __future__ import annotations

from app.brokers.base import AggregatorProvider, NotConfiguredError
from app.brokers.mock import MockAggregator
from app.core.config import Settings, get_settings


def get_aggregator(settings: Settings | None = None) -> AggregatorProvider:
    s = settings or get_settings()
    provider = (s.aggregator_provider or "mock").lower()
    if provider == "mock":
        # PR 110 (C6): the mock writes TEVA/GOLD/BOND into the user's REAL book
        # (broker_sync upserts into "Personal"). Two bodyless POSTs did exactly
        # that on a production-shaped app. Sandbox only.
        if (s.environment or "").lower() == "production":
            raise NotConfiguredError("the mock aggregator is disabled in production: it would "
                                     "write sample holdings into your real book")
        return MockAggregator()
    if not s.broker_enabled:
        raise NotConfiguredError(
            f"Aggregator '{provider}' requires BROKER_ENABLED=true and credentials.")
    if provider == "plaid":
        from app.brokers.plaid import PlaidAggregator
        return PlaidAggregator()
    if provider == "yodlee":
        from app.brokers.yodlee import YodleeAggregator
        return YodleeAggregator()
    raise NotConfiguredError(f"Unknown aggregator provider '{provider}'.")
