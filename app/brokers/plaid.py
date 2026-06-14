"""Plaid aggregator adapter (scaffold).

Reads holdings via Plaid's Investments product. Inactive until credentials are
provided (PLAID_CLIENT_ID / PLAID_SECRET) and ``broker_enabled`` is true. The
method bodies show the intended shape without making live calls.
"""
from __future__ import annotations

import os

from app.brokers.base import AggregatorProvider, BrokerAccount, BrokerPosition, NotConfiguredError


class PlaidAggregator(AggregatorProvider):
    name = "plaid"

    def __init__(self) -> None:
        self.client_id = os.getenv("PLAID_CLIENT_ID")
        self.secret = os.getenv("PLAID_SECRET")
        if not (self.client_id and self.secret):
            raise NotConfiguredError(
                "Plaid selected but PLAID_CLIENT_ID / PLAID_SECRET are not set.")

    def get_accounts(self, access_ref: str) -> list[BrokerAccount]:  # pragma: no cover
        raise NotConfiguredError("Plaid live calls not implemented in the scaffold; "
                                 "wire /investments/holdings/get here.")

    def get_positions(self, access_ref: str, account_id: str) -> list[BrokerPosition]:  # pragma: no cover
        raise NotConfiguredError("Plaid live calls not implemented in the scaffold.")
