"""Pick the aggregator adapter from config. Mock always works (sandbox); real
providers require ``broker_enabled`` + their credentials."""
from __future__ import annotations

from app.brokers.base import AggregatorProvider, NotConfiguredError
from app.brokers.mock import MockAggregator
from app.core.config import Settings, get_settings


def get_aggregator(settings: Settings | None = None) -> AggregatorProvider:
    s = settings or get_settings()
    provider = (s.aggregator_provider or "mock").lower()
    if provider == "mock":
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
