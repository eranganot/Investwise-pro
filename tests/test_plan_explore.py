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


def test_war_room_tags_holding_vs_market_signals(monkeypatch):
    """Since Phase 2 the war room runs on grounded signals, not demo data, so a
    non-holding idea is sourced from the screener universe rather than a hardcoded
    HYPE. This still verifies the thing that mattered: each session is tagged
    'portfolio' or 'market' by whether the ticker is held."""
    from app.schemas.lag import LagObservation
    from app.schemas.state_machine import ActionType, Market

    def fake_build(candidates, **kw):
        return [
            LagObservation(ticker="TEVA", market=Market.NYSE, depth=3, spot_price=100,
                           listing_price=108, action_type=ActionType.BUY,
                           expected_return_pct=8, volatility_pct=14),          # held
            LagObservation(ticker="NEWIDEA", market=Market.NYSE, depth=1, spot_price=100,
                           listing_price=112, action_type=ActionType.BUY,
                           expected_return_pct=12, volatility_pct=40),         # not held
        ]
    monkeypatch.setattr("app.api.routes.war_room.signal_service.build_observations", fake_build)
    monkeypatch.setattr("app.api.routes.war_room.signal_service.candidate_set",
                        lambda positions, **kw: [{"ticker": "TEVA", "market": "NYSE"},
                                                 {"ticker": "NEWIDEA", "market": "NYSE"}])
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        wr = c.get("/api/v1/war-room").json()
        assert wr["grounded"] is True
        by_ticker = {s["ticker"]: s for s in wr["sessions"]}
        assert by_ticker["TEVA"]["source"] == "portfolio"      # held
        assert by_ticker["NEWIDEA"]["source"] == "market"      # a new idea, not held
