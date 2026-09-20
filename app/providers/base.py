"""Abstract provider interfaces (Section AE - Dependency Inversion).

Core application logic depends only on these abstractions, never on a concrete
vendor (Polygon/AlphaVantage/IBKR). Concrete adapters are swapped via the registry.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.market import EconomicEvent, FXRate, Quote
from app.schemas.screener import Fundamentals


class MarketDataProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def get_quote(self, ticker: str) -> Quote: ...

    def get_history(self, ticker: str, days: int = 252) -> list[tuple[str, float]]:
        """Daily (date, close) history, oldest..newest. Default: none."""
        return []

    def get_intraday(self, ticker: str, span: str = "1d") -> list[tuple[str, float]]:
        """(ISO timestamp, price) bars within the last session ("1d") or five
        sessions ("5d"). Optional: a provider without intraday data raises, and
        the caller falls back to daily closes and says so."""
        raise NotImplementedError(f"{type(self).__name__} has no intraday data")

    def get_fundamentals(self, ticker: str) -> Fundamentals | None:
        """Valuation / growth / quality / income snapshot. Default: unsupported."""
        return None


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
