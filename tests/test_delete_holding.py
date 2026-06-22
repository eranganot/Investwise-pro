"""Removing a holding from the portfolio."""
import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi.testclient import TestClient
import app.main as m

SAMPLE = {"entity_name": "Personal", "positions": [
    {"ticker": "TEVA", "market": "NYSE", "asset_class": "Equities", "depth": 3,
     "spot_price": 100, "listing_price": 108, "quantity": 300, "cost_basis": 75},
    {"ticker": "GOLD", "market": "SPOT", "asset_class": "Commodities", "depth": 1,
     "spot_price": 100, "listing_price": 103, "quantity": 60, "cost_basis": 96}]}


def test_delete_removes_holding_and_404s_when_missing():
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json=SAMPLE)
        before = [p["ticker"] for p in c.get("/api/v1/portfolio").json()["positions"]]
        assert "GOLD" in before and "id" in c.get("/api/v1/portfolio").json()["positions"][0]
        r = c.delete("/api/v1/portfolio/position", params={"ticker": "GOLD", "market": "SPOT"})
        assert r.status_code == 200 and r.json()["deleted"] == 1
        after = [p["ticker"] for p in c.get("/api/v1/portfolio").json()["positions"]]
        assert "GOLD" not in after and "TEVA" in after
        assert c.delete("/api/v1/portfolio/position", params={"ticker": "NOPE"}).status_code == 404


def test_edit_updates_holding():
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json=SAMPLE)
        pos = c.get("/api/v1/portfolio").json()["positions"]
        teva = next(p for p in pos if p["ticker"] == "TEVA")
        r = c.put(f"/api/v1/portfolio/position/{teva['id']}",
                  json={"ticker": "TEVA", "market": "NASDAQ", "asset_class": "Equities",
                        "quantity": 250, "cost_basis": 80, "current_price": 120})
        assert r.status_code == 200
        body = r.json()
        assert body["market"] == "NASDAQ" and body["quantity"] == 250 and body["current_price"] == 120
        # persisted
        after = next(p for p in c.get("/api/v1/portfolio").json()["positions"] if p["ticker"] == "TEVA")
        assert after["market"] == "NASDAQ" and after["quantity"] == 250
        # missing id -> 404
        assert c.put("/api/v1/portfolio/position/nope-id", json={"quantity": 1}).status_code == 404
