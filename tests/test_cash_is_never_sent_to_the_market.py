"""Your cash balance must never be looked up as the NASDAQ ticker "CASH".

"CASH" is a real listed symbol -- Pathward Financial. The book stores liquid
shekels as a position whose ticker is the string "CASH", so any loop that walks
the book and asks the market about each row asks about a bank stock.

MEASURED ON PRODUCTION, 2026-09-20, HEAD 84be80b:

    POST /api/v1/portfolio/performance
        end_value_ils       147,014.61
    GET  /api/v1/portfolio
        nav_ils              22,148.23     cash 1,661.12
    147,014.61 - (22,148.23 - 1,661.12) = 126,527.50
    126,527.50 / 1,661.12              = 76.17
    GET  /api/v1/market/quote?ticker=CASH  ->  76.17 USD (NasdaqGS)

So the performance series valued 1,661 shekels as 1,661 Pathward shares -- ~86%
of the "book" it measured -- and every figure derived from it (total return,
max drawdown, excess vs SPY, the solver's seeded risk ceiling, the agents'
context) described a bank stock. Separately, card rec_1e8c9d on 2026-09-17:
"CASH is in a downtrend", from `_momentum_recs` fetching Pathward's history.

WHY IT WAS SILENT: Yahoo answers "CASH" with a perfectly plausible series, so
nothing raised; and /portfolio, the screen that is looked at, prices cash
correctly because `pricing_service` alone had a guard.

THE CHECK THE DATA CAN FAIL: a provider that answers for ANY symbol (the builtin
one does), spied on, driven through every book-level loop the app has. If "CASH"
reaches the provider from anywhere, this fails -- whichever loop added it.

Not financial advice.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.providers.registry as registry
from app.main import app
from app.providers.builtin import BuiltinMarketDataProvider


class _Spy:
    """Delegates to the builtin provider and records every symbol asked about."""

    name = "spy"

    def __init__(self):
        self.inner = BuiltinMarketDataProvider()
        self.asked: list[tuple[str, str]] = []

    def get_quote(self, ticker):
        self.asked.append(("quote", ticker))
        return self.inner.get_quote(ticker)

    def get_history(self, ticker, days=252):
        self.asked.append(("history", ticker))
        return self.inner.get_history(ticker, days)

    def get_intraday(self, ticker):
        self.asked.append(("intraday", ticker))
        return self.inner.get_intraday(ticker)

    def get_fundamentals(self, ticker):
        self.asked.append(("fundamentals", ticker))
        return self.inner.get_fundamentals(ticker)


def _pos(ticker, market, qty, price, basis, asset_class="Equities"):
    return {"ticker": ticker, "market": market, "asset_class": asset_class, "depth": 2,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": basis, "expected_return_pct": 8, "volatility_pct": 18}


BOOK = [_pos("QQQ", "NASDAQ", 3, 700.0, 650.0),
        _pos("BND", "NASDAQ", 10, 72.0, 70.0, asset_class="Fixed Income")]
CASH_ILS = 1661.12


@pytest.fixture()
def spy(monkeypatch):
    s = _Spy()
    registry._tiers.cache_clear()          # no cached answer may hide a call
    monkeypatch.setattr(registry, "market_provider", lambda: s)
    yield s
    registry._tiers.cache_clear()


@pytest.fixture()
def client(spy):
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": BOOK})
        c.put("/api/v1/plan", json={"objective": "Grow", "cash_target_pct": 7.5})
        c.post("/api/v1/portfolio/cash", json={"amount_ils": CASH_ILS})
        spy.asked.clear()                  # intake may quote; only the loops count
        yield c


def _cash_calls(spy):
    return [(kind, tk) for kind, tk in spy.asked if (tk or "").upper() == "CASH"]


def test_the_book_really_has_a_cash_row(client):
    """Otherwise every assertion below is vacuous."""
    rows = client.get("/api/v1/portfolio").json()["positions"]
    assert any(r["ticker"] == "CASH" and r["current_price"] == 1.0 for r in rows), rows


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/v1/portfolio/performance"),
    ("POST", "/api/v1/portfolio/performance?range=MAX"),
    ("GET", "/api/v1/recommendations"),
    ("GET", "/api/v1/rules/suggestions"),
    ("GET", "/api/v1/portfolio/position-history?ticker=QQQ&range=1M"),
])
def test_no_loop_asks_the_market_about_cash(client, spy, method, path):
    r = client.request(method, path)
    assert r.status_code < 500, f"{path}: {r.status_code} {r.text[:300]}"
    assert spy.asked, f"{path} asked the provider nothing -- the spy is not wired"
    bad = _cash_calls(spy)
    assert not bad, (f"{path} sent the cash balance to the market as the ticker "
                     f"'CASH' (Pathward Financial): {bad}")


def test_performance_values_cash_at_its_shekel_amount(client, spy):
    """The reconciliation that caught it on production, as an assertion."""
    perf = client.post("/api/v1/portfolio/performance").json()
    assert perf.get("ok"), perf
    assert "CASH" not in perf["holdings_analyzed"]
    assert perf["cash_flat_value"] == pytest.approx(CASH_ILS, abs=0.01)
    # Cash must be IN the series, as a flat amount: dropping it would overstate
    # every return and drawdown by the weight of the risky holdings. The book is
    # ~₪4,000 of securities (at builtin prices) plus the cash, never ~₪126k.
    nav = client.get("/api/v1/portfolio").json()["nav_ils"]
    assert perf["end_value"] > CASH_ILS
    assert perf["end_value"] < nav * 3, (perf["end_value"], nav)


def test_no_card_calls_cash_a_trending_security(client):
    cards = client.get("/api/v1/recommendations").json().get("recommendations", [])
    bad = [c["title"] for c in cards if "cash" in c.get("title", "").lower()
           and c.get("dimension") in ("momentum", "risk")]
    assert not bad, bad
