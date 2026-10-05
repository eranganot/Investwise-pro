"""PR 101: backtests read dividend-adjusted history; charts and rules do not.

Every backtest read plain closes, so a fund's dividends were invisible. BND
measured -1.76%/yr over ten years on closes and +1.25% with dividends, which is
what made "sell all of BND" beat holding it.
"""
import json

from app.providers import registry
from app.services import backtest_service as bs
from app.services import holding_review as hr
from tests._review_fixtures import series


def _chart(closes, adj=None):
    ind = {"quote": [{"close": closes}]}
    if adj is not None:
        ind["adjclose"] = [{"adjclose": adj}]
    return json.dumps({"chart": {"result": [{
        "timestamp": [1_700_000_000 + 86_400 * i for i in range(len(closes))],
        "indicators": ind}]}})


def test_yahoo_total_return_reads_adjclose_and_plain_history_does_not(monkeypatch):
    from app.providers import live
    monkeypatch.setattr(live, "_http_text", lambda url: _chart([10.0, 10.0, 10.0], [9.0, 9.5, 10.0]))
    p = live.YahooMarketDataProvider()
    assert [c for _, c in p.get_total_return_history("BND", 3)] == [9.0, 9.5, 10.0]
    assert [c for _, c in p.get_history("BND", 3)] == [10.0, 10.0, 10.0]


def test_yahoo_without_adjclose_is_empty_not_an_error(monkeypatch):
    from app.providers import live
    monkeypatch.setattr(live, "_http_text", lambda url: _chart([10.0, 11.0, 12.0]))
    assert live.YahooMarketDataProvider().get_total_return_history("USO", 3) == []


class _P:
    name = "fake"

    def __init__(self, adjusted):
        self.adjusted = adjusted

    def get_history(self, ticker, days=252):
        return [(f"d{i}", 10.0) for i in range(days)]

    def get_total_return_history(self, ticker, days=252):
        return [(f"d{i}", 10.0 + i) for i in range(days)] if self.adjusted else []


def test_the_registry_labels_what_it_returned(monkeypatch):
    # 200 days is served from the 260-day bucket, sliced to its last 200.
    for adjusted, basis, first in ((True, registry.TOTAL_RETURN, 70.0),
                                   (False, registry.PRICE_ONLY, 10.0)):
        registry._tiers.cache_clear()
        monkeypatch.setattr(registry, "market_provider", lambda a=adjusted: _P(a))
        try:
            rows, got = registry.guarded_total_return_history("BND", 200)
            assert got == basis and len(rows) == 200 and rows[0][1] == first
            # Charts, stops and trend rules keep the price you trade at.
            assert registry.guarded_history("BND", 200)[0][1] == 10.0
        finally:
            registry._tiers.cache_clear()


def test_fetch_reports_each_tickers_basis(monkeypatch):
    def _hist(tk, days):
        rows = [(f"2020-01-{i:02d}", 1.0 + i) for i in range(1, 6)]
        return rows, (registry.PRICE_ONLY if tk == "USO" else registry.TOTAL_RETURN)
    monkeypatch.setattr(bs, "guarded_total_return_history", _hist)
    basis: dict = {}
    series_, missing = bs._fetch(["BND", "USO"], basis=basis)
    assert sorted(series_) == ["BND", "USO"] and missing == []
    assert basis == {"BND": "total_return", "USO": "price_only"}
    assert bs.return_basis_of(basis) == {"return_basis": "price_only", "price_only_tickers": ["USO"]}
    assert bs.return_basis_of({"BND": "total_return"})["return_basis"] == "total_return"


def test_a_bond_fund_that_pays_its_return_as_dividends_is_not_a_loser():
    # Price drifts down ~1.7%/yr; a 3%/yr dividend is paid out of it. On plain
    # closes holding it "loses money"; on total return it earns.
    n = 760
    price = [100.0 * (1 - 0.017) ** (i / 252) for i in range(n)]
    adj = [p * (1.03) ** (i / 252) for i, p in enumerate(price)]
    plain = hr.measure_ticker("BND", series("BND", price), {"BND": "price_only"})
    total = hr.measure_ticker("BND", series("BND", adj), {"BND": "total_return"})
    assert plain["long"]["hold_cagr_pct"] < 0 < total["long"]["hold_cagr_pct"]
    assert plain["return_basis"] == "price_only" and total["return_basis"] == "total_return"


def test_old_measurements_are_out_of_date():
    assert bs.ENGINE_VERSION == "a6"
    ok, why = hr.fresh({"engine_version": "a5", "measured_at": "2026-10-05T00:00:00+00:00"})
    assert ok is False and "older engine" in why
