"""Built-in deterministic provider adapters (no external API keys required).

Swap in PolygonProvider / AlphaVantageProvider / IBKRProvider later behind the
same abstract interfaces; core logic is unaffected.
"""
from __future__ import annotations

import hashlib

from app.schemas.state_machine import MARKET_CURRENCY
from datetime import datetime, timezone

from app.providers.base import (
    BrokerProvider, EconomicDataProvider, FXProvider, MarketDataProvider,
)
from app.schemas.market import EconomicEvent, FXRate, Quote
from app.schemas.screener import Fundamentals


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SECTORS = ["Technology", "Healthcare", "Financials", "Consumer", "Industrials",
            "Energy", "Communication", "Utilities", "Materials"]


def _seed(*parts: str) -> int:
    return int(hashlib.sha1("|".join(parts).encode()).hexdigest(), 16)


class BuiltinMarketDataProvider(MarketDataProvider):
    name = "builtin"

    def get_quote(self, ticker: str) -> Quote:
        price = 50 + _seed(ticker) % 45000 / 100.0  # deterministic 50.00 - 500.00
        market = "TASE" if ticker.upper().startswith("TA") else "NYSE"
        return Quote(ticker=ticker, market=market, price=round(price, 2),
                     currency=MARKET_CURRENCY.get(market, "USD"), as_of=_now())

    def get_history(self, ticker: str, days: int = 252) -> list[tuple[str, float]]:
        """Deterministic synthetic (date, close) walk on a shared calendar."""
        import numpy as np
        from datetime import date, timedelta
        n = max(days, 2)
        rng = np.random.default_rng(_seed(ticker) % (2 ** 32))
        rets = rng.normal(0.0003, 0.012, n)
        today = date(2026, 6, 12)
        out, price = [], 100.0
        for i, r in enumerate(rets):
            price *= (1.0 + float(r))
            d = (today - timedelta(days=(n - 1 - i))).isoformat()
            out.append((d, round(price, 4)))
        return out

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        """Deterministic, plausible fundamentals seeded off the ticker.

        Spread across the realistic range so the screener has real dispersion to
        rank against (some cheap, some expensive; some growers, some value).
        """
        s = _seed("fund", ticker)

        def pick(salt: str, lo: float, hi: float, dp: int = 2) -> float:
            frac = (_seed(salt, ticker) % 10_000) / 10_000.0
            return round(lo + frac * (hi - lo), dp)

        return Fundamentals(
            ticker=ticker.upper(),
            name=ticker.upper(),
            sector=_SECTORS[s % len(_SECTORS)],
            pe=pick("pe", 7.0, 48.0, 1),
            pb=pick("pb", 0.8, 12.0, 1),
            earnings_growth_pct=pick("eg", -12.0, 42.0, 1),
            revenue_growth_pct=pick("rg", -6.0, 34.0, 1),
            profit_margin_pct=pick("pm", -4.0, 38.0, 1),
            roe_pct=pick("roe", 1.0, 46.0, 1),
            debt_to_equity=pick("de", 0.0, 240.0, 0),
            dividend_yield_pct=pick("dy", 0.0, 5.6, 2),
            as_of=_now(),
        )


class BuiltinFXProvider(FXProvider):
    """SYNTHETIC rates for tests and offline development. Not market data.

    `_BASE` is a frozen fixture, and it has drifted: 3.71 USD/ILS was written
    once and the market was ~2.98 on 2026-08-28 -- about 25% apart. That is
    harmless in a test, where a constant is exactly what is wanted, and it is a
    24% valuation error on a real book.

    `config.py` DEFAULTS `fx_provider` to "builtin", so a deployment that does
    not set FX_PROVIDER converts every foreign holding at these numbers with
    nothing on screen to say so. Production sets it -- verified 2026-08-30, the
    live app reports an implied USD/ILS of 2.9728 -- but a fresh environment, or
    a lost variable, would silently inherit this table. `registry.fx_provider()`
    logs a warning when this provider is the one selected, because "the rates
    are made up" is not something a reader should have to infer from source.
    """

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
    """No events, because this adapter has no source of them.

    IT USED TO RETURN FOUR, hardcoded, and they were the most confidently
    presented thing on the screen: *"US CPI release above consensus; rate-path
    implications."* *"USD/ILS broke a multi-week range."* Invented, unchanged
    since the day they were typed, carrying severity scores of 95 and 80.

    What made them harmful is everything TRUE that was wrapped around them.
    `market_impact.annotate` named the user's actual holdings in each one and
    computed the actual shekel exposure -- "touches 36% of your portfolio
    (₪7,634)" -- `_playbook` turned each into advice (*"Consider hedging the
    currency or rebalancing toward ILS assets"*), and the card printed
    `last_refreshed` above it. That field comes from `market_status()`: the
    PRICE refresh clock. So a frozen list wore a moving timestamp and read as
    market intelligence that had updated minutes ago.

    Every layer was honest by itself. The exposure arithmetic is right, the FX
    normalisation is right, the playbook is a defensible mapping from event type
    to action. They were composed on top of four sentences that were not true,
    and the composition is what reached the user.

    `economic_provider()` has never had an alternative -- it returns this class
    unconditionally -- so this feature has never once been real.

    THE FIX IS TO RETURN NOTHING, not to label the fiction. The frozen FX table
    above survives being labelled because it has a job: some number must value a
    dollar holding, and a stale one with a loud warning beats a crash. There is
    no equivalent argument here. Nothing needs an economic event to exist, so
    the honest count is zero and `/research/events` says so in the payload
    rather than leaving the card to invent an explanation.

    Wiring a real provider brings the panel back unchanged: `annotate`,
    `_playbook` and the exposure maths are untouched and are the part worth
    keeping. See STATUS.md PR 58.
    """

    name = "builtin"

    def get_events(self) -> list[EconomicEvent]:
        return []


class BuiltinBrokerProvider(BrokerProvider):
    name = "builtin"

    def get_positions(self) -> list[dict]:
        return []  # no live brokerage link in the built-in adapter
