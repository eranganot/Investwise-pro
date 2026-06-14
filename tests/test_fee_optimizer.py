"""Phase 3.2 - fee & expense-ratio optimizer."""
from fastapi.testclient import TestClient

from app.engines.fee_engine import FeeEngine
from app.main import app

HIGH_FEE = {"entity_name": "Personal", "positions": [
    {"ticker": "ACTIVE", "market": "NYSE", "asset_class": "Equities", "depth": 1,
     "spot_price": 100, "listing_price": 100, "quantity": 1000, "cost_basis": 90,
     "expense_ratio_pct": 1.20},
    {"ticker": "CHEAP", "market": "NYSE", "asset_class": "Equities", "depth": 1,
     "spot_price": 100, "listing_price": 100, "quantity": 10, "cost_basis": 90,
     "expense_ratio_pct": 0.05}]}


def test_engine_flags_high_fee_and_computes_saving():
    rep = FeeEngine().scan([
        {"ticker": "ACTIVE", "asset_class": "Equities", "value_ils": 100_000, "expense_ratio_pct": 1.20},
        {"ticker": "CHEAP", "asset_class": "Equities", "value_ils": 1_000, "expense_ratio_pct": 0.05}])
    assert rep.scanned == 2 and len(rep.findings) == 1
    fnd = rep.findings[0]
    assert fnd.ticker == "ACTIVE"
    # 100k * (1.20% - 0.22%) = 980/yr
    assert abs(fnd.annual_saving_ils - 980.0) < 0.5
    assert fnd.alternative.expense_ratio_pct < fnd.current_expense_ratio_pct


def test_no_fee_data_is_skipped():
    rep = FeeEngine().scan([{"ticker": "X", "asset_class": "Equities", "value_ils": 50_000}])
    assert rep.scanned == 0 and rep.findings == []


def test_fees_endpoint_and_feed_integration():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=HIGH_FEE)
        rep = c.get("/api/v1/fees").json()
        assert rep["total_annual_saving_ils"] > 0
        assert any(f["ticker"] == "ACTIVE" for f in rep["findings"])
        recs = c.get("/api/v1/recommendations").json()["recommendations"]
        fee_rec = next((r for r in recs if r["dimension"] == "fees"), None)
        assert fee_rec is not None
        at = fee_rec["audit_trail"]
        assert at["formulas"] and "expense_ratio" in at["formulas"][0]["expr"]
        assert any("taxable" in x.lower() for x in at["adversary"]["findings"])
        # cleanup shared DB
        for tk in ("ACTIVE", "CHEAP"):
            c.delete("/api/v1/portfolio/position", params={"ticker": tk, "market": "NYSE"})
