"""Phase 2.2 - What-If sliders feed agent state and re-evaluate live."""
from fastapi.testclient import TestClient

from app.main import app

PORT = {"entity_name": "Personal", "positions": [
    {"ticker": "TEVA", "market": "NYSE", "depth": 3, "spot_price": 100, "listing_price": 108,
     "quantity": 300, "cost_basis": 75, "expected_return_pct": 9, "volatility_pct": 14},
    {"ticker": "BOND", "market": "TASE", "asset_class": "Fixed Income", "depth": 1, "spot_price": 100,
     "listing_price": 99, "quantity": 100, "cost_basis": 120, "expected_return_pct": 3, "volatility_pct": 5}]}


def test_risk_tolerance_slider_changes_vetoes():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        low = c.post("/api/v1/whatif", json={"risk_tolerance": "Low"}).json()
        high = c.post("/api/v1/whatif", json={"risk_tolerance": "High"}).json()
        # Low caps volatility at 10% (< TEVA's 14%) -> TEVA vetoed; High (25%) lets it through.
        assert "TEVA" in low["risk_profile"]["vetoed_tickers"]
        assert "TEVA" not in high["risk_profile"]["vetoed_tickers"]
        assert low["risk_profile"]["volatility_cap_pct"] == 10.0
        assert high["risk_profile"]["volatility_cap_pct"] == 25.0


def test_drawdown_slider_scales_scenario_loss():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        mild = c.post("/api/v1/whatif", json={"expected_drawdown_pct": 10}).json()
        severe = c.post("/api/v1/whatif", json={"expected_drawdown_pct": 50}).json()
        assert severe["scenario"]["projected_loss_ils"] > mild["scenario"]["projected_loss_ils"]
        assert severe["risk_profile"]["max_drawdown_cap_pct"] == 50.0


def test_tlh_target_filters_qualifying_opportunities():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        low_t = c.post("/api/v1/whatif", json={"tlh_target_ils": 0}).json()
        high_t = c.post("/api/v1/whatif", json={"tlh_target_ils": 10_000_000}).json()
        assert low_t["tax_loss_harvesting"]["qualifying_count"] >= high_t["tax_loss_harvesting"]["qualifying_count"]


def test_whatif_defaults_run_without_body():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        r = c.post("/api/v1/whatif").json()
        assert r["inputs"]["risk_tolerance"] == "Medium"
        assert "risk_profile" in r and "scenario" in r
