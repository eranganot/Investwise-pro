"""Tests for ROI/Yield targets, goal projector, mix check, war-room market ideas."""
from fastapi.testclient import TestClient

from app.main import app

PORT = {"entity_name": "Personal", "positions": [
    {"ticker": "TEVA", "market": "NYSE", "depth": 3, "spot_price": 100, "listing_price": 108,
     "quantity": 300, "cost_basis": 75, "expected_return_pct": 9, "volatility_pct": 14},
    {"ticker": "BOND", "market": "TASE", "depth": 1, "spot_price": 100, "listing_price": 99,
     "quantity": 200, "cost_basis": 101, "expected_return_pct": 3, "volatility_pct": 5}]}


def test_plan_roi_target_compared_to_portfolio():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        c.put("/api/v1/plan", json={"objective": "Balanced", "risk_tolerance": "Medium",
                                    "target_roi_pct": 2, "target_roi_period": "quarterly"})
        pl = c.get("/api/v1/plan").json()
        assert pl["target_roi_pct"] == 2 and pl["target_roi_period"] == "quarterly"
        assert pl["roi_annual_target_pct"] == 8  # 2% quarterly -> 8% annual
        assert pl["portfolio_expected_roi_pct"] is not None
        assert isinstance(pl["roi_on_track"], bool)


def test_goal_projection():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        c.put("/api/v1/plan", json={"horizon_years": 10, "target_amount": 100000})
        proj = c.get("/api/v1/plan/projection").json()
        assert proj["years"] == 10 and proj["projected_median"] > 0
        assert proj["on_track"] in (True, False)


def test_mix_check_classifies_and_compares():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        mix = c.get("/api/v1/mix").json()
        assert "Fixed Income" in mix["current_allocation"]  # BOND classified as fixed income
        assert "Equities" in mix["current_allocation"]
        assert "rebalance_required" in mix


def test_war_room_includes_market_ideas_with_sources():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        wr = c.get("/api/v1/war-room").json()
        sources = {s["source"] for s in wr["sessions"]}
        assert "market" in sources  # HYPE etc. always surfaced
        assert any(s["ticker"] == "HYPE" and s["outcome"] == "VETOED" for s in wr["sessions"])
