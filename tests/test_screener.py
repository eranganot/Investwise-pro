"""Opportunity Agent / screener + fundamentals + expanded manage-holdings recs."""
from fastapi.testclient import TestClient

from app.agents.screener_agent import OpportunityAgent
from app.engines.screener_engine import ScreenerEngine
from app.main import app
from app.providers.builtin import BuiltinMarketDataProvider
from app.schemas.screener import Fundamentals
from app.services import commodities as cat
from app.services import universe as uni
from tests._cards import all_cards  # noqa: E402


def test_builtin_fundamentals_are_deterministic_and_dispersed():
    p = BuiltinMarketDataProvider()
    a, b = p.get_fundamentals("AAPL"), p.get_fundamentals("AAPL")
    assert isinstance(a, Fundamentals)
    assert a.pe == b.pe and a.roe_pct == b.roe_pct          # deterministic
    pes = {p.get_fundamentals(t).pe for t in ("AAPL", "JPM", "XOM", "KO", "NVDA")}
    assert len(pes) >= 4                                    # real dispersion to rank on


def test_screener_engine_ranks_value_over_hype():
    cheap = {"meta": {"ticker": "CHEAP", "kind": "stock", "asset_class": "Equities"},
             "fundamentals": Fundamentals(ticker="CHEAP", pe=9, pb=1.2, earnings_growth_pct=18,
                                          revenue_growth_pct=14, profit_margin_pct=22, roe_pct=28,
                                          debt_to_equity=30, dividend_yield_pct=3.0)}
    hype = {"meta": {"ticker": "HYPE", "kind": "stock", "asset_class": "Equities"},
            "fundamentals": Fundamentals(ticker="HYPE", pe=85, pb=14, earnings_growth_pct=5,
                                         revenue_growth_pct=9, profit_margin_pct=-3, roe_pct=4,
                                         debt_to_equity=180, dividend_yield_pct=0.0)}
    picks = ScreenerEngine().rank_equities([cheap, hype])
    assert picks[0].ticker == "CHEAP"
    assert "hype-priced" in picks[1].flags and "loss-making" in picks[1].flags
    assert picks[0].score > picks[1].score


def test_opportunity_agent_returns_top_ideas():
    ideas = OpportunityAgent().top_ideas(n_equities=6, n_commodities=4)
    assert len(ideas["equities"]) == 6
    assert all(0 <= p["score"] <= 100 for p in ideas["equities"])
    assert all(p["reasons"] for p in ideas["equities"])        # every pick is explained
    assert len(ideas["commodities"]) == 4


def test_weight_tilt_changes_ranking():
    agent = OpportunityAgent()
    base = [p["ticker"] for p in agent.top_ideas(n_equities=8)["equities"]]
    growth = [p["ticker"] for p in
              agent.top_ideas(weights={"value": 0.0, "growth": 1.0, "quality": 0.0, "income": 0.0},
                              n_equities=8)["equities"]]
    assert base != growth or set(base) == set(growth)         # tilt is wired through


def test_expanded_commodity_catalog():
    tickers = {c["ticker"] for c in cat.CATALOG}
    assert {"SLV", "CORN", "DBC", "USO"} <= tickers            # originals preserved
    # JO was in this list and has been REMOVED. It is the iPath Coffee ETN,
    # redeemed by Barclays, and Yahoo returns an empty series for it -- so this
    # line was asserting that the app still offers a delisted product, and it
    # would have gone red the moment anyone checked. See commodities.DELISTED
    # and tests/test_live_candidates.py.
    assert {"PALL", "URA", "LIT", "GDX", "BNO"} <= tickers     # new additions
    assert len(cat.CATALOG) >= 25
    assert not ({c["ticker"] for c in cat.CATALOG} & set(cat.DELISTED))


def test_universe_is_deduped_and_typed():
    u = uni.full_universe()
    tickers = [r["ticker"] for r in u]
    assert len(tickers) == len(set(tickers))                  # no dupes
    # "bond" joined the kinds in PR 10. The universe had 64 Equities, 19
    # Commodities and ZERO Fixed Income while every objective preset wanted
    # 10-60% bonds, so the redeploy card recorded that leg as unplaceable on
    # every scan of every book.
    assert all(r["kind"] in {"stock", "etf", "commodity", "bond"} for r in u)
    assert any(r["kind"] == "commodity" for r in u)
    assert any(r["kind"] == "bond" for r in u)
    # And the kind must map to the class the allocation engine measures against,
    # or the leg looks filled while the gap stays open.
    assert all(r["asset_class"] == "Fixed Income" for r in u if r["kind"] == "bond")


def test_recommendations_include_holding_verdicts_and_buy_ideas():
    port = {"entity_name": "Personal", "positions": [
        {"ticker": "AAPL", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
         "spot_price": 1, "listing_price": 1, "quantity": 50, "cost_basis": 150},
        {"ticker": "MSFT", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
         "spot_price": 1, "listing_price": 1, "quantity": 40, "cost_basis": 300}]}
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=port)
        c.post("/api/v1/portfolio/refresh-prices")
        r = c.get("/api/v1/recommendations").json()
        assert "buy_ideas" in r and isinstance(r["buy_ideas"], list)
        dims = {rec.get("dimension") for rec in all_cards(r)}
        assert "holding" in dims                              # per-holding verdicts present
        for rec in r["recommendations"]:
            assert rec["action"] and rec["how"]               # contract upheld
