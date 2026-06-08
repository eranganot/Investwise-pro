"""Built-in deterministic provider adapters (no external API keys required).

Swap in PolygonProvider / AlphaVantageProvider / IBKRProvider later behind the
same abstract interfaces; core logic is unaffected.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from app.providers.base import (
    BrokerProvider, EconomicDataProvider, FXProvider, MarketDataProvider,
)
from app.schemas.market import EconomicEvent, FXRate, Quote


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed(*parts: str) -> int:
    return int(hashlib.sha1("|".join(parts).encode()).hexdigest(), 16)


class BuiltinMarketDataProvider(MarketDataProvider):
    name = "builtin"

    def get_quote(self, ticker: str) -> Quote:
        price = 50 + _seed(ticker) % 45000 / 100.0  # deterministic 50.00 - 500.00
        market = "TASE" if ticker.upper().startswith("TA") else "NYSE"
        return Quote(ticker=ticker, market=market, price=round(price, 2),
                     currency="ILS" if market == "TASE" else "USD", as_of=_now())


class BuiltinFXProvider(FXProvider):
    name = "builtin"
    _BASE = {("USD", "ILS"): 3.71, ("EUR", "ILS"): 4.02, ("USD", "EUR"): 0.92}

    def get_rate(self, base: str, quote: str) -> FXRate:
        base, quote = base.upper(), quote.upper()
        rate = self._BASE.get((base, quote))
        if rate is None:
            inv = self._BASE.get((quote, base))
            rate = round(1 / inv, 4) if inv else 1.0
        return FXRate(base=base, quote=quote, rate=rate, as_of=_now())


class BuiltinEconomicDataProvider(EconomicDataProvider):
    name = "builtin"

    def get_events(self) -> list[EconomicEvent]:
        return [
            EconomicEvent(event_type="REGULATORY_SURTAX_UPDATE",
                          description="Israeli surtax threshold guidance revised for the tax year.",
                          affected_assets=["TASE:TA35", "ILS:USD"], horizon="MEDIUM", severity=95),
            EconomicEvent(event_type="MACRO_CPI_PRINT",
                          description="US CPI release above consensus; rate-path implications.",
                          affected_assets=["NYSE:SPY", "USD:ILS"], horizon="SHORT", severity=80),
            EconomicEvent(event_type="EARNINGS",
                          description="TA-35 constituent earnings cluster this week.",
                          affected_assets=["TASE:TA35"], horizon="SHORT", severity=70),
            EconomicEvent(event_type="FX_MOVE",
                          description="USD/ILS broke a multi-week range; asset-location impact.",
                          affected_assets=["ILS:USD"], horizon="MEDIUM", severity=65),
        ]


class BuiltinBrokerProvider(BrokerProvider):
    name = "builtin"

    def get_positions(self) -> list[dict]:
        return []  # no live brokerage link in the built-in adapter
