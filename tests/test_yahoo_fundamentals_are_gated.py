"""The equity screener returned nothing, for months, and nothing said so.

THE FIRST TEST THIS CODE PATH HAS EVER HAD. `YahooMarketDataProvider.
get_fundamentals` was added on 2026-06-22 and every fixture in
`test_market_data_live.py` is a `chart` payload -- prices. `test_screener.py`
exercises `BuiltinMarketDataProvider`, whose fundamentals are a sha1 of the
ticker. So the live fundamentals path shipped untested and stayed untested.

WHAT WAS ACTUALLY WRONG, measured 2026-08-30 against production and directly:

    GET /v10/finance/quoteSummary/AAPL  -> 401 {"code":"Unauthorized",
                                                "description":"Invalid Crumb"}
    GET /v8/finance/chart/AAPL          -> 200

Yahoo gated quoteSummary behind a session cookie plus a matching crumb. Prices
use `chart`, which is not gated -- so NAV, the refresh job and every price on
the screen kept working perfectly while the screener produced zero equity
ideas. The production log, once PR 24 added the counter, said it plainly:

    equity candidates the provider says have no fundamentals: AAPL, ABBV,
    ADBE, AMD, AMZN, AVGO, BA, BAC, BRK-B, ... XOM      (all 58, every scan)

Eran's buy ideas had been three commodities and three bonds. No stocks.

TWO THINGS ARE FIXED HERE AND THE SECOND MATTERS MORE
================================================================================

1. The cookie + crumb handshake, fetched once and reused, re-fetched only on a
   401 so a rotated crumb costs one retry rather than a whole scan.

2. **A transport failure and an empty symbol stopped being the same answer.**
   `get_fundamentals` returned None for a gated endpoint, a network error, and
   a symbol Yahoo genuinely has nothing for. `_fundamentals_for` dropped the
   candidate either way. That is how a 401 on 58 of 58 candidates went unseen:
   the code was designed to skip quietly. Now auth and transport failures RAISE
   -- the screener counts them as `unreachable` and logs the whole set -- and
   None means only "Yahoo answered, there is nothing here".

   Without (2), (1) would work until the day Yahoo changes the handshake again,
   and then fail exactly as silently as before.

Every test here is offline. The handshake is mocked so CI never depends on
Yahoo being reachable or on the crumb format staying the same.
"""
import json

import pytest

from app.providers import live as L


@pytest.fixture(autouse=True)
def _clean_session():
    """Each test starts with no cached crumb and leaves none behind."""
    L.YahooMarketDataProvider._reset_session()
    yield
    L.YahooMarketDataProvider._reset_session()


_GOOD = json.dumps({"quoteSummary": {"result": [{
    "summaryDetail": {"trailingPE": {"raw": 36.6}, "priceToSalesTrailing12Months": {"raw": 9.1},
                      "dividendYield": {"raw": 0.0034}},
    "defaultKeyStatistics": {"priceToBook": {"raw": 43.4}},
    "financialData": {"returnOnEquity": {"raw": 1.4875}, "profitMargins": {"raw": 0.24},
                      "debtToEquity": {"raw": 154.5}, "earningsGrowth": {"raw": 0.12},
                      "revenueGrowth": {"raw": 0.06}},
    "summaryProfile": {"sector": "Technology"},
}], "error": None}})

_EMPTY = json.dumps({"quoteSummary": {"result": [], "error": None}})
_UNAUTHORIZED = json.dumps({"finance": {"result": None, "error": {
    "code": "Unauthorized", "description": "Invalid Crumb"}}})


def _wire(monkeypatch, script):
    """Drive _http_text from a list of (url_substring, response-or-exception).

    Returns the call log so a test can assert WHAT was requested, not merely
    what came back -- the crumb has to actually be on the query string.
    """
    calls = []

    def fake(url, timeout=10.0, *, opener=None, user_agent="InvestWise/1.0"):
        calls.append({"url": url, "ua": user_agent, "opener": opener})
        for frag, resp in script:
            if frag in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp() if callable(resp) else resp
        raise AssertionError(f"unscripted request: {url}")

    monkeypatch.setattr(L, "_http_text", fake)
    return calls


# --------------------------------------------------------------------------
# The handshake
# --------------------------------------------------------------------------

def test_a_request_carries_the_crumb_and_a_browser_user_agent(monkeypatch):
    calls = _wire(monkeypatch, [
        ("fc.yahoo.com", ""),
        ("getcrumb", "AbCd1234xyz"),
        ("quoteSummary", _GOOD),
    ])
    f = L.YahooMarketDataProvider().get_fundamentals("AAPL")
    assert f is not None

    qs = [c for c in calls if "quoteSummary" in c["url"]]
    assert qs, "no quoteSummary request was made"
    assert "crumb=AbCd1234xyz" in qs[0]["url"], (
        f"the crumb never reached the query string: {qs[0]['url']}")
    assert qs[0]["ua"] == L._BROWSER_UA, (
        "Yahoo checks the User-Agent as well as the cookie")
    assert qs[0]["opener"] is not None, (
        "the request went out without the cookie jar, which is answered 401")


def test_the_crumb_is_fetched_once_and_reused(monkeypatch):
    """A scan is 58 candidates. Re-handshaking per name would triple the calls."""
    calls = _wire(monkeypatch, [
        ("fc.yahoo.com", ""),
        ("getcrumb", "C1"),
        ("quoteSummary", _GOOD),
    ])
    p = L.YahooMarketDataProvider()
    for t in ("AAPL", "MSFT", "NVDA", "KO"):
        p.get_fundamentals(t)

    assert sum(1 for c in calls if "getcrumb" in c["url"]) == 1, (
        "the crumb was re-fetched per candidate")
    assert sum(1 for c in calls if "quoteSummary" in c["url"]) == 4


def test_an_expired_crumb_is_retried_once_then_gives_up_loudly(monkeypatch):
    """A rotated crumb should cost one retry, not a silent empty ranking."""
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] == 1:
            raise OSError("HTTP Error 401: Unauthorized")
        return _GOOD

    calls = _wire(monkeypatch, [
        ("fc.yahoo.com", ""),
        ("getcrumb", lambda: f"C{state['n']}"),
        ("quoteSummary", flaky),
    ])
    f = L.YahooMarketDataProvider().get_fundamentals("AAPL")
    assert f is not None, "a single expired crumb should have been recovered"
    assert sum(1 for c in calls if "getcrumb" in c["url"]) == 2, (
        "the session was not refreshed after the 401")


def test_a_persistent_401_RAISES_rather_than_returning_none(monkeypatch):
    """THE ONE THAT WOULD HAVE CAUGHT THE LIVE BUG.

    Before this change a gated endpoint returned None, `_fundamentals_for`
    dropped the candidate, and 58 of 58 failures looked exactly like 58 tickers
    with nothing to report. Raising is what makes the screener count them as
    unreachable and log the entire set.
    """
    _wire(monkeypatch, [
        ("fc.yahoo.com", ""),
        ("getcrumb", "C1"),
        ("quoteSummary", OSError("HTTP Error 401: Unauthorized")),
    ])
    with pytest.raises(Exception):
        L.YahooMarketDataProvider().get_fundamentals("AAPL")


def test_an_error_object_in_the_body_also_raises(monkeypatch):
    """Yahoo can answer 200 with an error payload. That is not 'no data'."""
    body = json.dumps({"quoteSummary": {"result": None, "error": {
        "code": "Unauthorized", "description": "Invalid Crumb"}}})
    _wire(monkeypatch, [
        ("fc.yahoo.com", ""), ("getcrumb", "C1"), ("quoteSummary", body),
    ])
    with pytest.raises(ValueError, match="quoteSummary error"):
        L.YahooMarketDataProvider().get_fundamentals("AAPL")


def test_an_empty_result_still_returns_none(monkeypatch):
    """The one case None is allowed to mean: Yahoo answered, nothing there."""
    _wire(monkeypatch, [
        ("fc.yahoo.com", ""), ("getcrumb", "C1"), ("quoteSummary", _EMPTY),
    ])
    assert L.YahooMarketDataProvider().get_fundamentals("NOSUCH") is None


def test_a_missing_crumb_is_refused_rather_than_sent(monkeypatch):
    """An HTML error page is not a crumb, and must not be pasted into a URL."""
    _wire(monkeypatch, [
        ("fc.yahoo.com", ""),
        ("getcrumb", "<!DOCTYPE html><html>Too Many Requests</html>"),
    ])
    with pytest.raises(ValueError, match="no usable crumb"):
        L.YahooMarketDataProvider().get_fundamentals("AAPL")


def test_the_parsed_fields_are_the_ones_the_screener_scores(monkeypatch):
    """Changing HOW it is fetched must not change WHAT comes back.

    ScreenerEngine ranks on pe, pb, earnings/revenue growth, profit margin, roe,
    debt/equity and dividend yield. A field silently arriving as None is scored
    as a missing factor, which `_mean` then drops -- so the shape matters as
    much as the fetch.
    """
    _wire(monkeypatch, [
        ("fc.yahoo.com", ""), ("getcrumb", "C1"), ("quoteSummary", _GOOD),
    ])
    f = L.YahooMarketDataProvider().get_fundamentals("AAPL")

    assert f.pe == pytest.approx(36.6)
    assert f.pb == pytest.approx(43.4)
    assert f.roe_pct == pytest.approx(148.75), "ROE must be a percentage, not a ratio"
    assert f.dividend_yield_pct == pytest.approx(0.34), "yield must be a percentage"
    assert f.profit_margin_pct == pytest.approx(24.0)
    assert f.earnings_growth_pct == pytest.approx(12.0)
    assert f.revenue_growth_pct == pytest.approx(6.0)
    assert f.sector == "Technology"


# --------------------------------------------------------------------------
# The screener's side of the contract
# --------------------------------------------------------------------------

def test_the_screener_counts_a_gated_provider_as_unreachable(monkeypatch):
    """End to end: the failure is now COUNTED, which is the whole point.

    This is the assertion that would have turned a months-long silence into a
    warning on the first scan.
    """
    from app.agents.screener_agent import OpportunityAgent

    def boom(_ticker):
        raise OSError("HTTP Error 401: Unauthorized")

    monkeypatch.setattr("app.agents.screener_agent.guarded_fundamentals", boom)

    cands = [{"ticker": t, "name": t, "market": "NASDAQ", "kind": "stock",
              "asset_class": "Equities"} for t in ("AAPL", "MSFT", "NVDA")]
    ag = OpportunityAgent()
    picks = ag.screen_equities(top_n=5, candidates=cands)

    assert picks == [], "a gated provider cannot produce rankings"
    cov = ag.last_coverage
    assert cov["ranked"] == 0
    assert sorted(cov["unreachable"]) == ["AAPL", "MSFT", "NVDA"], (
        "an auth failure was filed as 'no data', which is what made this "
        "invisible for months")
    assert cov["no_data"] == []
