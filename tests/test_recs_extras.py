"""War-room timestamps + benchmark-lag and commodity-sleeve recommendations."""
import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
from fastapi.testclient import TestClient

import app.main as m
from app.services import recommendations as R


def test_benchmark_rec_fires_when_trailing():
    perf = {"ok": True, "benchmark": "SPY", "excess_return_pct": -8.2,
            "benchmark_return_pct": 12.0, "total_return_pct": 3.8}
    recs = R._benchmark_recs(perf, "Balanced")
    assert len(recs) == 1
    c = recs[0]
    assert c["dimension"] == "performance" and "SPY" in c["title"]
    assert c["apply"]["kind"] == "none" and c["why"] and c["impact"]
    assert c["meta"]["excess_pct"] == -8.2 and c["severity"] == "MEDIUM"
    assert R._benchmark_recs({"ok": True, "excess_return_pct": -14.0,
                              "benchmark_return_pct": 10, "total_return_pct": -4},
                             "Balanced")[0]["severity"] == "HIGH"


def test_benchmark_rec_silent_when_ahead_or_no_data():
    assert R._benchmark_recs({"ok": True, "excess_return_pct": 2.0}, "Balanced") == []
    assert R._benchmark_recs({"ok": True, "excess_return_pct": -1.0}, "Balanced") == []
    assert R._benchmark_recs({"ok": False}, "Balanced") == []
    assert R._benchmark_recs(None, "Balanced") == []


def test_commodity_sleeve_rec_via_api():
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "AAA", "market": "NYSE", "asset_class": "Equities", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 100, "cost_basis": 100},
            {"ticker": "BBB", "market": "TASE", "asset_class": "Equities", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 100, "cost_basis": 100}]})
        c.put("/api/v1/plan", json={"objective": "Balanced", "risk_tolerance": "Medium"})
        recs = c.get("/api/v1/recommendations").json()["recommendations"]
        # Since Phase 3 the title carries the size, e.g. "Add a commodities
        # sleeve - ₪1,200 of DBC": a card without a number isn't an action.
        card = next((r for r in recs if r["title"].startswith("Add a commodities sleeve")), None)
        assert card is not None
        assert card["dimension"] == "diversification" and card["how"]
        assert card["est_amount"] and card["est_amount"] > 0     # sized, not vague
        assert card["meta"]["chosen"]                            # one named pick


def test_commodity_rec_silent_when_already_held():
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "AAA", "market": "NYSE", "asset_class": "Equities", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 80, "cost_basis": 100},
            {"ticker": "GLD", "market": "NYSE", "asset_class": "Commodities", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 20, "cost_basis": 100}]})
        c.put("/api/v1/plan", json={"objective": "Balanced", "risk_tolerance": "Medium"})
        recs = c.get("/api/v1/recommendations").json()["recommendations"]
        assert not any(r["title"] == "Add a commodities sleeve" for r in recs)


def test_war_room_carries_timestamps():
    with TestClient(m.app) as c:
        w = c.get("/api/v1/war-room").json()
        assert w.get("generated_at"), "expected a run timestamp"
        assert w["sessions"], "expected market-idea sessions even with no holdings"
        assert all(s.get("decided_at") == w["generated_at"] for s in w["sessions"])
