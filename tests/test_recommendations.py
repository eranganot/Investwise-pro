"""Actionable recommendations + plan-driven agents.

Since Phase 2 the war room derives its signals from real price history rather
than from the spot/listing values typed in at intake, so these tests inject the
observation directly -- that keeps them focused on what they actually assert
(risk tolerance vetoes, depth tilts conviction) instead of on price synthesis.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.lag import LagObservation
from app.schemas.state_machine import ActionType, Market


@pytest.fixture
def observed(monkeypatch):
    """Force the war room to see one specific, grounded-looking observation."""
    def _set(**kw):
        obs = LagObservation(**kw)
        monkeypatch.setattr("app.api.routes.war_room.signal_service.build_observations",
                            lambda *a, **k: [obs])
        monkeypatch.setattr("app.api.routes.war_room.signal_service.candidate_set",
                            lambda *a, **k: [{"ticker": obs.ticker, "market": obs.market.value}])
        return obs
    return _set

CONC = {"entity_name": "Personal", "positions": [
    {"ticker": "TEVA", "market": "NYSE", "depth": 3, "spot_price": 100, "listing_price": 108,
     "quantity": 300, "cost_basis": 75, "expected_return_pct": 9, "volatility_pct": 14},
    {"ticker": "BOND", "market": "TASE", "asset_class": "Fixed Income", "depth": 1, "spot_price": 100,
     "listing_price": 99, "quantity": 100, "cost_basis": 120, "expected_return_pct": 3, "volatility_pct": 5}]}


def test_recommendations_are_actionable():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=CONC)
        r = c.get("/api/v1/recommendations").json()
        titles = [x["title"] for x in r["recommendations"]]
        assert any("Trim TEVA" in t for t in titles)        # concentration
        assert any("Harvest" in t for t in titles)          # tax loss
        for rec in r["recommendations"]:
            assert rec["action"] and rec["how"]             # what + how present


def test_risk_tolerance_changes_agent_decision(observed):
    observed(ticker="TEVA", market=Market.NYSE, depth=3, spot_price=100,
             listing_price=108, action_type=ActionType.BUY,
             expected_return_pct=9, volatility_pct=14)
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=CONC)
        def teva():
            wr = c.get("/api/v1/war-room").json()
            return next(s["outcome"] for s in wr["sessions"] if s["ticker"] == "TEVA")
        c.put("/api/v1/plan", json={"risk_tolerance": "Medium"})
        assert teva() == "DISPLAYED"
        c.put("/api/v1/plan", json={"risk_tolerance": "Low"})   # vol 14% > 10% cap
        assert teva() == "VETOED"


def test_preferred_depth_flavor_tilts_conviction(observed):
    observed(ticker="GOLD", market=Market.SPOT, depth=1, spot_price=100,
             listing_price=104, action_type=ActionType.REBALANCE, volatility_pct=8)
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "GOLD", "market": "SPOT", "asset_class": "Commodities", "depth": 1,
             "spot_price": 100, "listing_price": 104, "quantity": 60, "cost_basis": 96, "volatility_pct": 8}]})
        def conv():
            g = next(s for s in c.get("/api/v1/war-room").json()["sessions"] if s["ticker"] == "GOLD")
            return [l for l in g["transcript"] if l["agent"] == "Decision"][0]["detail"]["scores"]["conviction"]
        c.put("/api/v1/plan", json={"preferred_depth": None}); base = conv()
        c.put("/api/v1/plan", json={"preferred_depth": 1}); tilted = conv()
        assert tilted > base
