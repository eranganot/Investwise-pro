"""Abstract provider interfaces (Section AE - Dependency Inversion).

Core application logic depends only on these abstractions, never on a concrete
vendor (Polygon/AlphaVantage/IBKR). Concrete adapters are swapped via the registry.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.market import EconomicEvent, FXRate, Quote


class MarketDataProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def get_quote(self, ticker: str) -> Quote: ...


class FXProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def get_rate(self, base: str, quote: str) -> FXRate: ...


class EconomicDataProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def get_events(self) -> list[EconomicEvent]: ...


class BrokerProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def get_positions(self) -> list[dict]: ...
