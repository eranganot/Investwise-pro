"""Live, no-key market-data providers (Phase B).

Real US/global EOD prices via Stooq and real FX via Frankfurter (ECB) - both
free and key-less. HTTP runs in a worker thread so it never blocks the event
loop, and the registry already wraps every call in the cache/rate-limit/breaker
tier. The deterministic ``builtin`` provider stays the default for tests/offline.
"""
from __future__ import annotations

import concurrent.futures
import json
import urllib.request
from datetime import datetime, timezone

from app.providers.base import FXProvider, MarketDataProvider
from app.schemas.market import FXRate, Quote


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _http_text(url: str, timeout: float = 10.0) -> str:
    """Blocking GET in a worker thread (keeps the event loop free)."""
    def _call() -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "InvestWise/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_call).result(timeout=timeout + 5)


class YahooMarketDataProvider(MarketDataProvider):
    """EOD/last quotes from Yahoo Finance's public chart API (no key).

    Plain US tickers work as-is (AAPL, VOO); international listings use Yahoo
    suffixes (e.g. TEVA.TA for Tel Aviv, VOD.L for London).
    """
    name = "yahoo"

    @staticmethod
    def to_symbol(ticker: str) -> str:
        return ticker.strip().upper()

    def get_quote(self, ticker: str) -> Quote:
        sym = self.to_symbol(ticker)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"
        data = json.loads(_http_text(url))
        result = ((data.get("chart") or {}).get("result") or [])
        if not result:
            raise ValueError(f"unknown symbol '{ticker}' on Yahoo")
        meta = result[0].get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None:
            raise ValueError(f"no price for '{ticker}'")
        ts = meta.get("regularMarketTime")
        as_of = (datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else _now())
        return Quote(ticker=sym, market=str(meta.get("fullExchangeName") or meta.get("exchangeName") or "US"),
                     price=round(float(price), 4), currency=str(meta.get("currency") or "USD"), as_of=as_of)


class FrankfurterFXProvider(FXProvider):
    """ECB reference FX rates from Frankfurter (no key)."""
    name = "frankfurter"

    def get_rate(self, base: str, quote: str) -> FXRate:
        base, quote = base.upper(), quote.upper()
        if base == quote:
            return FXRate(base=base, quote=quote, rate=1.0, as_of=_now())
        data = json.loads(_http_text(f"https://api.frankfurter.app/latest?from={base}&to={quote}"))
        rate = (data.get("rates") or {}).get(quote)
        if rate is None:
            raise ValueError(f"no FX rate {base}->{quote}")
        return FXRate(base=base, quote=quote, rate=float(rate), as_of=data.get("date") or _now())
