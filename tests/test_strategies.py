"""Strategy catalog + apply (preset + rebalance) + load-basket."""
from fastapi.testclient import TestClient

from app.main import app
from app.services import strategies as cat


def test_catalog_grouped_by_goal():
    bg = cat.by_goal()
    assert set(bg) == {"Grow", "Balanced", "Income", "Preserve"}
    assert all(s["basket"] and abs(sum(w for _, w in s["basket"]) - 1.0) < 1e-6 for s in cat.CATALOG)
    assert all(abs(sum(s["target_allocation"].values()) - 1.0) < 1e-6 for s in cat.CATALOG)


def test_strategies_endpoint():
    with TestClient(app) as c:
        r = c.get("/api/v1/strategies").json()
        assert r["goals"][0] == "Grow"
        assert any(s["id"] == "grow_ai_semis" for s in r["by_goal"]["Grow"])


def test_apply_presets_plan_and_returns_rebalance():
    port = {"entity_name": "Personal", "positions": [
        {"ticker": "BND", "market": "NYSE", "asset_class": "Fixed Income", "depth": 1,
         "spot_price": 1, "listing_price": 1, "quantity": 100, "cost_basis": 70}]}
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=port)
        c.post("/api/v1/portfolio/refresh-prices")
        r = c.post("/api/v1/strategies/grow_ai_semis/apply").json()
        assert r["ok"] and r["strategy"]["objective"] == "Grow"
        # plan now reflects the strategy preset
        plan = c.get("/api/v1/plan").json()
        assert plan["objective"] == "Grow" and plan.get("risk_tolerance") == "High"
        # all-bonds book vs an all-equities target -> a BUY-equities rebalance suggestion
        assert any(a["asset_class"] == "Equities" for a in r["rebalance_actions"])


def test_load_basket_replaces_holdings():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "OLD", "market": "NYSE", "depth": 1, "spot_price": 1, "listing_price": 1,
             "quantity": 5, "cost_basis": 1}]})
        r = c.post("/api/v1/strategies/bal_6040/load-basket", json={"total": 10000}).json()
        assert r["ok"] and r["count"] == 2  # VTI + BND
        tickers = {p["ticker"] for p in c.get("/api/v1/portfolio").json()["positions"]}
        assert "OLD" not in tickers and {"VTI", "BND"} <= tickers


def test_unknown_strategy_is_rejected():
    with TestClient(app) as c:
        assert c.post("/api/v1/strategies/nope/apply").json()["ok"] is False
