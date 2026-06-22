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

    def get_history(self, ticker: str, days: int = 252) -> list[float]:
        sym = self.to_symbol(ticker)
        rng = "1y" if days <= 260 else ("2y" if days <= 520 else "5y")
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
               f"?interval=1d&range={rng}")
        data = json.loads(_http_text(url))
        result = ((data.get("chart") or {}).get("result") or [])
        if not result:
            raise ValueError(f"no history for '{ticker}'")
        ts = result[0].get("timestamp") or []
        closes = (((result[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
        out: list[tuple[str, float]] = []
        for t, c in zip(ts, closes):
            if c is None:
                continue
            out.append((datetime.fromtimestamp(t, timezone.utc).date().isoformat(), float(c)))
        if len(out) < 2:
            raise ValueError(f"insufficient history for '{ticker}'")
        return out[-days:]

    def get_fundamentals(self, ticker: str):
        """Valuation/growth/quality/income via Yahoo quoteSummary (no key).

        Returns None (rather than raising) when the data is unavailable or the
        endpoint is gated, so the screener can simply skip the name.
        """
        from app.schemas.screener import Fundamentals
        sym = self.to_symbol(ticker)
        url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
               "?modules=summaryDetail,defaultKeyStatistics,financialData,summaryProfile")
        try:
            data = json.loads(_http_text(url))
        except Exception:
            return None
        result = ((data.get("quoteSummary") or {}).get("result") or [])
        if not result:
            return None
        node = result[0]
        sd = node.get("summaryDetail") or {}
        ks = node.get("defaultKeyStatistics") or {}
        fd = node.get("financialData") or {}
        sp = node.get("summaryProfile") or {}

        def raw(d: dict, key: str):
            v = d.get(key)
            if isinstance(v, dict):
                v = v.get("raw")
            return v if isinstance(v, (int, float)) else None

        def pct(d: dict, key: str):
            v = raw(d, key)
            return round(v * 100.0, 2) if v is not None else None

        return Fundamentals(
            ticker=sym,
            name=str(sp.get("longName") or sym),
            sector=str(sp.get("sector") or "Unknown"),
            pe=raw(sd, "trailingPE"),
            pb=raw(ks, "priceToBook"),
            earnings_growth_pct=pct(fd, "earningsGrowth"),
            revenue_growth_pct=pct(fd, "revenueGrowth"),
            profit_margin_pct=pct(fd, "profitMargins"),
            roe_pct=pct(fd, "returnOnEquity"),
            debt_to_equity=raw(fd, "debtToEquity"),
            dividend_yield_pct=pct(sd, "dividendYield"),
            as_of=_now(),
        )


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


class FMPMarketDataProvider(MarketDataProvider):
    """Financial Modeling Prep (KEYED): real quotes, history AND fundamentals.

    This is the provider that lights up the screener with real P/E, growth,
    margins, ROE, debt and dividend yield. To enable it set an API key and
    select it:

        FMP_API_KEY=your_key   (or the ``fmp_api_key`` setting)
        MARKET_DATA_PROVIDER=fmp

    The free tier is rate-limited, so the registry's cache/breaker tier in front
    of every call matters. Fundamentals degrade gracefully (return None) so a
    quota hit or missing field just drops that name from the screen.
    """
    name = "fmp"
    BASE = "https://financialmodelingprep.com/api/v3"

    @staticmethod
    def to_symbol(ticker: str) -> str:
        return ticker.strip().upper()

    def _key(self) -> str:
        import os
        from app.core.config import get_settings
        return get_settings().fmp_api_key or os.getenv("FMP_API_KEY", "")

    def _get(self, path: str):
        key = self._key()
        if not key:
            raise ValueError("FMP_API_KEY not set (set fmp_api_key or the env var)")
        sep = "&" if "?" in path else "?"
        return json.loads(_http_text(f"{self.BASE}/{path}{sep}apikey={key}"))

    def get_quote(self, ticker: str) -> Quote:
        sym = self.to_symbol(ticker)
        data = self._get(f"quote/{sym}")
        if not data:
            raise ValueError(f"no quote for '{ticker}' on FMP")
        q = data[0]
        price = q.get("price")
        if price is None:
            raise ValueError(f"no price for '{ticker}'")
        return Quote(ticker=sym, market=str(q.get("exchange") or "US"),
                     price=round(float(price), 4), currency="USD", as_of=_now())

    def get_history(self, ticker: str, days: int = 252) -> list[tuple[str, float]]:
        sym = self.to_symbol(ticker)
        data = self._get(f"historical-price-full/{sym}?serietype=line&timeseries={max(days, 2)}")
        hist = (data or {}).get("historical") or []
        out = [(h["date"], float(h["close"])) for h in hist if h.get("close") is not None]
        if len(out) < 2:
            raise ValueError(f"insufficient history for '{ticker}'")
        out.sort(key=lambda x: x[0])  # FMP returns newest-first; we want oldest..newest
        return out[-days:]

    def get_fundamentals(self, ticker: str):
        """Real fundamentals via FMP ratios-ttm + financial-growth + profile.

        Returns None on any failure so the screener simply skips the name.
        """
        from app.schemas.screener import Fundamentals
        sym = self.to_symbol(ticker)
        try:
            ratios = (self._get(f"ratios-ttm/{sym}") or [{}])[0]
            growth = (self._get(f"financial-growth/{sym}?limit=1") or [{}])
            growth = growth[0] if growth else {}
            profile = (self._get(f"profile/{sym}") or [{}])[0]
        except Exception:
            return None

        def pct(v):
            return round(v * 100.0, 2) if isinstance(v, (int, float)) else None

        def num(v):
            return round(float(v), 2) if isinstance(v, (int, float)) else None

        de = ratios.get("debtEquityRatioTTM")
        return Fundamentals(
            ticker=sym,
            name=str(profile.get("companyName") or sym),
            sector=str(profile.get("sector") or "Unknown"),
            pe=num(ratios.get("peRatioTTM")),
            pb=num(ratios.get("priceToBookRatioTTM")),
            earnings_growth_pct=pct(growth.get("netIncomeGrowth")),
            revenue_growth_pct=pct(growth.get("revenueGrowth")),
            profit_margin_pct=pct(ratios.get("netProfitMarginTTM")),
            roe_pct=pct(ratios.get("returnOnEquityTTM")),
            debt_to_equity=(round(de * 100.0, 1) if isinstance(de, (int, float)) else None),
            dividend_yield_pct=pct(ratios.get("dividendYieldTTM")),
            as_of=_now(),
        )
