"""Vendor-agnostic aggregation interface + DTOs.

`AggregatorProvider` is the read-only seam (accounts + positions). Real adapters
(Plaid, Yodlee) implement it; the `mock` adapter makes the flow testable offline.
`BrokerProvider` is the future order-placement seam, stubbed here so the registry
and data model are ready when live trading is enabled.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class NotConfiguredError(RuntimeError):
    """Raised when a real provider is selected without credentials / enablement."""


class BrokerAccount(BaseModel):
    account_id: str
    institution: str
    currency: str = "ILS"
    type: str = "brokerage"


class BrokerPosition(BaseModel):
    ticker: str
    market: str = "OTHER"
    quantity: float = Field(ge=0)
    cost_basis: float = Field(ge=0)          # per-share
    current_price: float = Field(ge=0)       # per-share
    currency: str = "ILS"
    asset_class: str | None = None


class AggregatorProvider(ABC):
    """Read-only holdings aggregation (Plaid / Yodlee / mock)."""
    name: str = "base"

    @abstractmethod
    def get_accounts(self, access_ref: str) -> list[BrokerAccount]: ...

    @abstractmethod
    def get_positions(self, access_ref: str, account_id: str) -> list[BrokerPosition]: ...


class BrokerProvider(ABC):
    """Order placement seam (future phase - not enabled in the scaffold)."""
    name: str = "base"

    @abstractmethod
    def place_order(self, account_id: str, order: dict) -> dict: ...
