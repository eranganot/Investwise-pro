"""Phase 2.1 - every recommendation carries a transparent audit trail."""
from fastapi.testclient import TestClient

from app.main import app

PORT = {"entity_name": "Personal", "positions": [
    {"ticker": "TEVA", "market": "NYSE", "depth": 3, "spot_price": 100, "listing_price": 108,
     "quantity": 300, "cost_basis": 75, "expected_return_pct": 9, "volatility_pct": 14},
    {"ticker": "BOND", "market": "TASE", "asset_class": "Fixed Income", "depth": 1, "spot_price": 100,
     "listing_price": 99, "quantity": 100, "cost_basis": 120, "expected_return_pct": 3, "volatility_pct": 5}]}


def test_every_recommendation_has_audit_trail():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        recs = c.get("/api/v1/recommendations").json()["recommendations"]
        assert recs
        for r in recs:
            at = r["audit_trail"]
            # all three required sections present
            assert "raw_data" in at and at["raw_data"]
            assert at["formulas"] and all(fm["expr"] for fm in at["formulas"])
            assert "adversary" in at and "critique" in at["adversary"]


def test_trim_formula_shows_substituted_numbers():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        recs = c.get("/api/v1/recommendations").json()["recommendations"]
        trim = next(r for r in recs if "Trim" in r["title"])
        trim_fml = next(fm for fm in trim["audit_trail"]["formulas"] if fm["name"] == "Trim amount")
        assert "(weight - cap) * NAV" in trim_fml["expr"]
        assert trim_fml["substituted"] and trim_fml["result"].startswith("₪")


def test_tax_rec_adversary_flags_wash_sale():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        recs = c.get("/api/v1/recommendations").json()["recommendations"]
        tax = next(r for r in recs if "Harvest" in r["title"])
        findings = " ".join(tax["audit_trail"]["adversary"]["findings"]).lower()
        assert "wash-sale" in findings
