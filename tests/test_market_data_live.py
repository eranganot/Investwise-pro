"""Phase B - live market-data (Yahoo) + FX (Frankfurter) + price refresh (HTTP mocked)."""
import pytest
from fastapi.testclient import TestClient

import app.providers.live as live
from app.main import app
from app.providers.live import FrankfurterFXProvider, YahooMarketDataProvider

_YAHOO = ('{"chart":{"result":[{"meta":{"currency":"USD","symbol":"AAPL",'
          '"regularMarketPrice":201.5,"regularMarketTime":1781308800,'
          '"fullExchangeName":"NasdaqGS"}}],"error":null}}')
_YAHOO_EMPTY = '{"chart":{"result":[],"error":{"code":"Not Found"}}}'
_FX_JSON = '{"amount":1.0,"base":"USD","date":"2026-06-12","rates":{"ILS":3.69}}'


def test_yahoo_symbol_passthrough():
    assert YahooMarketDataProvider.to_symbol("aapl") == "AAPL"
    assert YahooMarketDataProvider.to_symbol("teva.ta") == "TEVA.TA"


def test_yahoo_parses_price(monkeypatch):
    monkeypatch.setattr(live, "_http_text", lambda url, timeout=10.0: _YAHOO)
    q = YahooMarketDataProvider().get_quote("AAPL")
    assert q.ticker == "AAPL" and q.price == 201.5 and q.currency == "USD"
    assert q.market == "NasdaqGS" and q.as_of.startswith("2026-")


def test_yahoo_unknown_symbol_raises(monkeypatch):
    monkeypatch.setattr(live, "_http_text", lambda url, timeout=10.0: _YAHOO_EMPTY)
    with pytest.raises(ValueError):
        YahooMarketDataProvider().get_quote("NOPE")


def test_frankfurter_fx(monkeypatch):
    monkeypatch.setattr(live, "_http_text", lambda url, timeout=10.0: _FX_JSON)
    r = FrankfurterFXProvider().get_rate("USD", "ILS")
    assert r.rate == 3.69 and r.base == "USD" and r.quote == "ILS"
    assert FrankfurterFXProvider().get_rate("USD", "USD").rate == 1.0


def test_registry_selects_live_provider(monkeypatch):
    from app.core.config import get_settings
    from app.providers import registry
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "yahoo")
    monkeypatch.setenv("FX_PROVIDER", "frankfurter")
    get_settings.cache_clear(); registry.market_provider.cache_clear(); registry.fx_provider.cache_clear()
    try:
        assert registry.market_provider().name == "yahoo"
        assert registry.fx_provider().name == "frankfurter"
    finally:
        get_settings.cache_clear(); registry.market_provider.cache_clear(); registry.fx_provider.cache_clear()


def test_refresh_prices_updates_holdings():
    port = {"entity_name": "Personal", "positions": [
        {"ticker": "AAPL", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
         "spot_price": 1, "listing_price": 1, "quantity": 10, "cost_basis": 150}]}
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=port)
        r = c.post("/api/v1/portfolio/refresh-prices").json()
        assert r["updated"] >= 1 and r["source"] == "builtin"
        assert "AAPL" in [x["ticker"] for x in r["prices"]]   # our holding was refreshed
        aapl = next(x for x in c.get("/api/v1/portfolio").json()["positions"] if x["ticker"] == "AAPL")
        assert aapl["current_price"] and aapl["current_price"] > 0
        c.delete("/api/v1/portfolio/position", params={"ticker": "AAPL", "market": "NASDAQ"})


def test_data_status_reports_builtin_as_illustrative():
    with TestClient(app) as c:
        ds = c.get("/api/v1/data-status").json()
        assert ds["live"] is False and ds["market_data_provider"] == "builtin"
        assert "Illustrative" in ds["label"]


def test_data_status_reports_live_when_provider_set(monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "yahoo")
    get_settings.cache_clear()
    try:
        with TestClient(app) as c:
            ds = c.get("/api/v1/data-status").json()
            assert ds["live"] is True and "Live" in ds["label"]
    finally:
        get_settings.cache_clear()
