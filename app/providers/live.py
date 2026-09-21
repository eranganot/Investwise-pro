"""Live, no-key market-data providers (Phase B).

Real US/global EOD prices via Stooq and real FX via Frankfurter (ECB) - both
free and key-less. HTTP runs in a worker thread so it never blocks the event
loop, and the registry already wraps every call in the cache/rate-limit/breaker
tier. The deterministic ``builtin`` provider stays the default for tests/offline.
"""
from __future__ import annotations

import concurrent.futures
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from app.providers.base import FXProvider, MarketDataProvider
from app.schemas.market import FXRate, Quote


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _http_text(url: str, timeout: float = 10.0, *, opener=None,
               user_agent: str = "InvestWise/1.0") -> str:
    """Blocking GET in a worker thread (keeps the event loop free).

    ``opener`` lets a caller supply a urllib opener carrying a cookie jar, which
    Yahoo's quoteSummary requires; a cookieless request is answered 401.
    """
    def _call() -> str:
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        _open = opener.open if opener is not None else urllib.request.urlopen
        with _open(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_call).result(timeout=timeout + 5)


# Yahoo checks the User-Agent as well as the cookie. Kept as a constant so the
# tests can assert it is actually sent rather than trusting one call site.
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


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
        # A ZERO IS NOT A PRICE. Yahoo returns a meta block with
        # `regularMarketPrice: 0` for symbols it half-resolves -- a delisted
        # line, a venue suffix it does not carry, a ticker that has changed. The
        # `is None` guard let that through as a real quote, and one refresh
        # later the holding was worth 0 in NAV, on the holdings card, and in
        # everything sized against NAV. Thirty minutes later a good quote
        # arrived and it repaired itself with no trace of what happened.
        #
        # Raising, rather than returning it, is what matters: the caller's
        # fallback provider then gets its turn, and `guarded_quote` does not
        # cache a poisoned zero for the rest of its TTL.
        if price is None or float(price) <= 0:
            raise ValueError(f"no usable price for '{ticker}' (Yahoo said {price!r})")
        ts = meta.get("regularMarketTime")
        as_of = (datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else _now())
        return Quote(ticker=sym, market=str(meta.get("fullExchangeName") or meta.get("exchangeName") or "US"),
                     price=round(float(price), 4), currency=str(meta.get("currency") or "USD"),
                     as_of=as_of, as_of_source=("market" if ts else "request"))

    def get_history(self, ticker: str, days: int = 252) -> list[float]:
        sym = self.to_symbol(ticker)
        # The ladder used to stop at "5y", so any caller asking for a decade got
        # five years and no warning -- fine for a 200-day trend, useless for
        # backtesting a strategy across more than one market regime.
        #
        # Deliberately capped at "10y": with interval=1d, range=max silently
        # returns MONTHLY bars (QQQ: 329 rows for 1999-2026 vs 2513 for 10y),
        # which a caller counting "days" would happily mistake for 27 years of
        # daily data and backtest against. 10y is the longest range Yahoo serves
        # at true daily granularity.
        rng = ("1y" if days <= 260 else "2y" if days <= 520 else "5y" if days <= 1300
               else "10y")
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

    def get_intraday(self, ticker: str, span: str = "1d") -> list[tuple[str, float]]:
        """5-minute bars for the last session, or 30-minute bars for five.

        The `chart` endpoint, which is not crumb-gated. Timestamps are UTC ISO
        strings; the caller decides how to label them."""
        sym = self.to_symbol(ticker)
        # 1d: 5-minute bars. 5d / 60d: 30-minute bars (60d is Yahoo's limit for
        # sub-hourly data, used by the ledger repair to price a past sale).
        interval = "5m" if span == "1d" else "30m"
        rng = span if span in ("1d", "5d", "60d") else "5d"
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
               f"?interval={interval}&range={rng}")
        data = json.loads(_http_text(url))
        result = ((data.get("chart") or {}).get("result") or [])
        if not result:
            raise ValueError(f"no intraday data for '{ticker}'")
        ts = result[0].get("timestamp") or []
        closes = (((result[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
        out = [(datetime.fromtimestamp(t, timezone.utc).isoformat(), float(c))
               for t, c in zip(ts, closes) if c is not None and float(c) > 0]
        if len(out) < 2:
            raise ValueError(f"insufficient intraday data for '{ticker}'")
        return out

    # --- quoteSummary is gated: cookie + crumb, cached across calls ---------
    #
    # MEASURED 2026-08-30, against production and directly:
    #
    #     GET /v10/finance/quoteSummary/AAPL   -> 401 {"code":"Unauthorized",
    #                                                  "description":"Invalid Crumb"}
    #     GET /v8/finance/chart/AAPL           -> 200
    #
    # Yahoo now requires a session cookie plus a matching crumb on quoteSummary.
    # Prices come from `chart`, which is NOT gated -- which is exactly why the
    # app looked healthy while the equity screener returned nothing at all. The
    # production log showed all 58 candidates in "no fundamentals", every scan.
    #
    # The crumb is fetched once and reused; it is only re-fetched when a call
    # comes back 401, so a rotated crumb costs one retry rather than a scan.
    _crumb: str | None = None
    _opener = None

    @classmethod
    def _reset_session(cls) -> None:
        cls._crumb = None
        cls._opener = None

    @classmethod
    def _session(cls) -> tuple[str, object]:
        """(crumb, opener). Fetched once, reused, refreshed only on a 401."""
        if cls._crumb and cls._opener is not None:
            return cls._crumb, cls._opener
        import http.cookiejar
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        # Any Yahoo origin will set the consent cookies the crumb is bound to.
        try:
            _http_text("https://fc.yahoo.com/", timeout=8.0,
                       opener=opener, user_agent=_BROWSER_UA)
        except Exception:  # noqa: BLE001 -- fc.yahoo.com answers 404 but still sets cookies
            pass
        crumb = _http_text("https://query1.finance.yahoo.com/v1/test/getcrumb",
                           timeout=8.0, opener=opener, user_agent=_BROWSER_UA).strip()
        if not crumb or len(crumb) > 64 or "<" in crumb:
            raise ValueError(f"Yahoo returned no usable crumb (got {crumb[:40]!r})")
        cls._crumb, cls._opener = crumb, opener
        return crumb, opener

    def get_fundamentals(self, ticker: str):
        """Valuation/growth/quality/income via Yahoo quoteSummary (cookie+crumb).

        THE TWO FAILURE MODES ARE NO LONGER THE SAME ANSWER, and that is the
        point of this change as much as the crumb is.

        This returned ``None`` for everything -- a gated endpoint, a network
        error, and a symbol Yahoo genuinely has no fundamentals for. The
        screener's `_fundamentals_for` then dropped the candidate either way, so
        "we could not reach the provider" and "this ticker has nothing" were
        indistinguishable. That is how a 401 on every one of 58 candidates went
        unnoticed: the app was designed to skip quietly.

        Now a transport or auth failure RAISES (the screener records it as
        `unreachable` and logs the whole set), and ``None`` means only what it
        should: Yahoo answered, and there is nothing there.
        """
        from app.schemas.screener import Fundamentals
        sym = self.to_symbol(ticker)

        def _fetch(crumb: str, opener) -> str:
            url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
                   "?modules=summaryDetail,defaultKeyStatistics,financialData,summaryProfile"
                   f"&crumb={urllib.parse.quote(crumb)}")
            return _http_text(url, opener=opener, user_agent=_BROWSER_UA)

        crumb, opener = self._session()
        try:
            body = _fetch(crumb, opener)
        except Exception:  # noqa: BLE001 -- most likely an expired crumb
            self._reset_session()
            crumb, opener = self._session()
            # A second failure is real: let it propagate so the caller counts it
            # as unreachable instead of silently ranking a smaller universe.
            body = _fetch(crumb, opener)

        data = json.loads(body)
        err = ((data.get("quoteSummary") or {}).get("error") or None)
        if err:
            raise ValueError(f"Yahoo quoteSummary error for '{ticker}': {err}")
        result = ((data.get("quoteSummary") or {}).get("result") or [])
        if not result:
            return None      # Yahoo ANSWERED: nothing for this symbol.
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

        def dividend_yield():
            """A company that pays nothing answers 0.0. It does not answer None.

            MEASURED against Yahoo, 2026-08-31:

                BRK-B  dividendYield {}   trailingAnnualDividendYield {'raw': 0.0}
                AMZN   dividendYield {}   trailingAnnualDividendYield {'raw': 0.0}
                JPM    dividendYield {'raw': 0.0168}

            Yahoo drops `dividendYield` entirely for a non-payer while still
            reporting, on the same object, that the trailing yield is zero. The
            adapter read only the first field, so a MEASURED zero was filed as
            "unknown" -- the same conflation PR 25 removed at the transport
            layer, surviving in the field mapping.

            It happened to produce the right ranking, because `_mean([None])`
            collapsed to 0.0 and 0.0 was the truth. That is luck, not
            correctness: the moment `_pillar` started scoring an unmeasured
            factor neutral (as it must), every non-payer would have been handed
            a middling income score it has not earned. Right answer, wrong
            route -- and the route is what breaks when the data shifts.

            A `None` from here now means only what it should: Yahoo did not say.
            """
            v = pct(sd, "dividendYield")
            if v is not None:
                return v
            return pct(sd, "trailingAnnualDividendYield")

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
            dividend_yield_pct=dividend_yield(),
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
    # The legacy /api/v3 endpoints were retired for new/free keys; FMP now serves
    # the "stable" API (query-param symbols). https://site.financialmodelingprep.com/developer/docs
    BASE = "https://financialmodelingprep.com/stable"

    @staticmethod
    def to_symbol(ticker: str) -> str:
        return ticker.strip().upper()

    def _key(self) -> str:
        import os
        from app.core.config import get_settings
        return get_settings().fmp_api_key or os.getenv("FMP_API_KEY", "")

    def _get(self, path: str, **params):
        key = self._key()
        if not key:
            raise ValueError("FMP_API_KEY not set (set fmp_api_key or the env var)")
        params["apikey"] = key
        qs = urllib.parse.urlencode(params)
        data = json.loads(_http_text(f"{self.BASE}/{path}?{qs}"))
        # FMP signals problems with an object (not a list): invalid/expired key,
        # exhausted quota, or a paid-only endpoint. Surface the real message
        # instead of crashing later on data[0].
        if isinstance(data, dict):
            msg = data.get("Error Message") or data.get("error") or data.get("message")
            if msg:
                raise ValueError(f"FMP: {msg}")
        return data

    def get_quote(self, ticker: str) -> Quote:
        sym = self.to_symbol(ticker)
        data = self._get("quote", symbol=sym)
        rows = data if isinstance(data, list) else [data]
        if not rows or not isinstance(rows[0], dict):
            raise ValueError(f"no quote for '{ticker}' on FMP")
        q = rows[0]
        price = q.get("price", q.get("previousClose"))
        # Same rule as the Yahoo adapter above, and FMP needs it more: it answers
        # an unknown or delisted symbol with `"price": 0` rather than an error,
        # so `is None` never fires and a zero walks in wearing a quote's clothes.
        if price is None or float(price) <= 0:
            raise ValueError(f"no usable price for '{ticker}' (FMP said {price!r})")
        # FMP reports the venue's last-trade epoch. Stamping `_now()` instead --
        # which this did -- makes every quote look freshly traded, so a delisted
        # instrument whose last print was 14 months ago is indistinguishable from
        # one that traded a second ago. Use the real timestamp when it's there,
        # and say so; fall back to "request" rather than pretending.
        ts = q.get("timestamp")
        as_of, src = _now(), "request"
        try:
            if ts is not None and float(ts) > 0:
                as_of = datetime.fromtimestamp(float(ts), timezone.utc).isoformat()
                src = "market"
        except (TypeError, ValueError, OSError, OverflowError):
            as_of, src = _now(), "request"
        return Quote(ticker=sym, market=str(q.get("exchange") or "US"),
                     price=round(float(price), 4), currency="USD",
                     as_of=as_of, as_of_source=src)

    def get_history(self, ticker: str, days: int = 252) -> list[tuple[str, float]]:
        sym = self.to_symbol(ticker)
        data = self._get("historical-price-eod/full", symbol=sym)
        # stable returns a flat list of {date, close, ...}; tolerate the older
        # {"historical": [...]} shape too.
        hist = data.get("historical") if isinstance(data, dict) else data
        out = [(h["date"], float(h["close"])) for h in (hist or [])
               if isinstance(h, dict) and h.get("close") is not None]
        if len(out) < 2:
            raise ValueError(f"insufficient history for '{ticker}'")
        out.sort(key=lambda x: x[0])  # FMP returns newest-first; we want oldest..newest
        return out[-days:]

    def get_fundamentals(self, ticker: str):
        """Real fundamentals via FMP stable ratios-ttm + financial-growth + profile.

        Returns None on any failure so the screener simply skips the name. Field
        names are looked up with fallbacks to tolerate stable/legacy differences.
        """
        from app.schemas.screener import Fundamentals
        sym = self.to_symbol(ticker)
        try:
            ratios = (self._get("ratios-ttm", symbol=sym) or [{}])[0]
            growth = (self._get("financial-growth", symbol=sym, limit=1) or [{}])
            growth = growth[0] if growth else {}
            profile = (self._get("profile", symbol=sym) or [{}])[0]
        except Exception:
            return None

        def first(d: dict, *keys):
            for k in keys:
                v = d.get(k)
                if isinstance(v, (int, float)):
                    return v
            return None

        def pct(v):
            return round(v * 100.0, 2) if isinstance(v, (int, float)) else None

        def num(v):
            return round(float(v), 2) if isinstance(v, (int, float)) else None

        de = first(ratios, "debtToEquityRatioTTM", "debtEquityRatioTTM", "debtToEquityTTM")
        return Fundamentals(
            ticker=sym,
            name=str(profile.get("companyName") or sym),
            sector=str(profile.get("sector") or "Unknown"),
            pe=num(first(ratios, "priceToEarningsRatioTTM", "peRatioTTM")),
            pb=num(first(ratios, "priceToBookRatioTTM", "pbRatioTTM")),
            earnings_growth_pct=pct(first(growth, "netIncomeGrowth", "epsgrowth", "epsGrowth")),
            revenue_growth_pct=pct(first(growth, "revenueGrowth")),
            profit_margin_pct=pct(first(ratios, "netProfitMarginTTM", "netIncomeMarginTTM")),
            roe_pct=pct(first(ratios, "returnOnEquityTTM")),
            debt_to_equity=(round(de * 100.0, 1) if isinstance(de, (int, float)) else None),
            dividend_yield_pct=pct(first(ratios, "dividendYieldTTM", "dividendYieldPercentageTTM")),
            as_of=_now(),
        )
