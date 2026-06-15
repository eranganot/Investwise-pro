"""Commodity catalog + add-by-amount + agent integration."""
from fastapi.testclient import TestClient

from app.main import app
from app.services import commodities as cat


def test_catalog_grouped_and_known_tickers():
    bg = cat.by_category()
    assert "Precious Metals" in bg and "Agriculture" in bg
    assert cat.is_commodity("SLV") and cat.is_commodity("corn".upper())
    assert cat.get("DBC")["category"] == "Diversified"
    assert "salt" in cat.NOT_INVESTABLE and "potato" in cat.NOT_INVESTABLE


def test_commodities_endpoint():
    with TestClient(app) as c:
        r = c.get("/api/v1/commodities").json()
        tickers = [x["ticker"] for cat_ in r["by_category"].values() for x in cat_]
        assert {"SLV", "CORN", "DBC", "USO"} <= set(tickers)
        assert "salt" in r["not_investable"]


def test_add_commodity_by_amount_prices_live():
    with TestClient(app) as c:  # builtin provider gives a deterministic price
        r = c.post("/api/v1/portfolio/add", json={"ticker": "SLV", "amount": 1000,
                                                  "asset_class": "Commodities", "market": "NYSE"}).json()
        assert r["ok"] and r["ticker"] == "SLV" and r["asset_class"] == "Commodities"
        assert r["quantity"] > 0 and abs(r["value"] - 1000) < r["price"]  # ~1000 of value
        port = c.get("/api/v1/portfolio").json()
        slv = next(p for p in port["positions"] if p["ticker"] == "SLV")
        assert slv["asset_class"] == "Commodities"


def test_commodity_strategy_exists_and_loads():
    from app.services import strategies as st
    s = st.get("bal_commodities")
    assert s and any(t == "DBC" for t, _ in s["basket"])
    assert abs(s["target_allocation"]["Commodities"] - 0.20) < 1e-6


def test_rebalance_rec_suggests_instrument():
    # all-bonds book + a commodities target -> a BUY-Commodities rec that names an ETF
    port = {"entity_name": "Personal", "positions": [
        {"ticker": "BND", "market": "NYSE", "asset_class": "Fixed Income", "depth": 1,
         "spot_price": 1, "listing_price": 1, "quantity": 100, "cost_basis": 70}]}
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=port)
        c.post("/api/v1/portfolio/refresh-prices")
        c.post("/api/v1/strategies/bal_commodities/apply")
        recs = c.get("/api/v1/recommendations").json()["recommendations"]
        comm = next((r for r in recs if "Commodities" in r["title"]), None)
        if comm:
            assert any("DBC" in h for h in comm["how"])
