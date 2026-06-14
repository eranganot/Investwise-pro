"""Deterministic in-memory aggregator - the sandbox we build & test against."""
from __future__ import annotations

from app.brokers.base import AggregatorProvider, BrokerAccount, BrokerPosition

_HOLDINGS = {
    "mock-acct-1": [
        BrokerPosition(ticker="TEVA", market="NYSE", quantity=300, cost_basis=75.0,
                       current_price=108.0, currency="USD", asset_class="Equities"),
        BrokerPosition(ticker="GOLD", market="SPOT", quantity=60, cost_basis=96.0,
                       current_price=104.0, currency="USD", asset_class="Commodities"),
        BrokerPosition(ticker="BOND", market="TASE", quantity=100, cost_basis=120.0,
                       current_price=99.0, currency="ILS", asset_class="Fixed Income"),
    ],
}


class MockAggregator(AggregatorProvider):
    name = "mock"

    def get_accounts(self, access_ref: str) -> list[BrokerAccount]:
        return [BrokerAccount(account_id="mock-acct-1", institution="Mock Brokerage",
                              currency="ILS", type="brokerage")]

    def get_positions(self, access_ref: str, account_id: str) -> list[BrokerPosition]:
        return list(_HOLDINGS.get(account_id, []))
