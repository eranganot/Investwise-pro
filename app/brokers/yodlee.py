"""Yodlee aggregator adapter (scaffold) - inactive until credentials exist."""
from __future__ import annotations

import os

from app.brokers.base import AggregatorProvider, BrokerAccount, BrokerPosition, NotConfiguredError


class YodleeAggregator(AggregatorProvider):
    name = "yodlee"

    def __init__(self) -> None:
        if not (os.getenv("YODLEE_CLIENT_ID") and os.getenv("YODLEE_SECRET")):
            raise NotConfiguredError(
                "Yodlee selected but YODLEE_CLIENT_ID / YODLEE_SECRET are not set.")

    def get_accounts(self, access_ref: str) -> list[BrokerAccount]:  # pragma: no cover
        raise NotConfiguredError("Yodlee live calls not implemented in the scaffold.")

    def get_positions(self, access_ref: str, account_id: str) -> list[BrokerPosition]:  # pragma: no cover
        raise NotConfiguredError("Yodlee live calls not implemented in the scaffold.")
