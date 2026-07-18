"""Phase 1: cash as a first-class holding.

Cash used to materialise only as a side effect of accepting a sell, so money you
already held was untrackable -- the allocation donut read 100% equities for a
book with real liquidity, and the liquidity score used a generic default.
"""
import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
from fastapi.testclient import TestClient
import app.main as m


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "TEVA", "market": "TASE", "asset_class": "Equities", "depth": 3,
         "spot_price": 100, "listing_price": 100, "quantity": 100, "cost_basis": 100,
         "expected_return_pct": 7, "volatility_pct": 14},
    ]})


def test_set_and_read_cash_round_trip():
    with TestClient(m.app) as c:
        _seed(c)
        assert c.get("/api/v1/portfolio/cash").json()["cash_ils"] == 0.0
        r = c.post("/api/v1/portfolio/cash", json={"amount_ils": 2500, "mode": "set"})
        assert r.status_code == 200 and r.json()["cash_ils"] == 2500.0
        assert c.get("/api/v1/portfolio/cash").json()["cash_ils"] == 2500.0
        # 'set' replaces rather than accumulating
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 1000, "mode": "set"})
        assert c.get("/api/v1/portfolio/cash").json()["cash_ils"] == 1000.0


def test_adjust_mode_adds_and_withdraws():
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 1000, "mode": "set"})
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 500, "mode": "adjust"})
        assert c.get("/api/v1/portfolio/cash").json()["cash_ils"] == 1500.0
        c.post("/api/v1/portfolio/cash", json={"amount_ils": -400, "mode": "adjust"})
        assert c.get("/api/v1/portfolio/cash").json()["cash_ils"] == 1100.0
        # over-withdrawing floors at zero rather than going negative
        c.post("/api/v1/portfolio/cash", json={"amount_ils": -9999, "mode": "adjust"})
        assert c.get("/api/v1/portfolio/cash").json()["cash_ils"] == 0.0


def test_set_rejects_negative_balance():
    with TestClient(m.app) as c:
        _seed(c)
        assert c.post("/api/v1/portfolio/cash",
                      json={"amount_ils": -100, "mode": "set"}).status_code == 422


def test_cash_flows_into_nav_and_portfolio_totals():
    with TestClient(m.app) as c:
        _seed(c)                                   # 100 x ₪100 = ₪10,000
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 2500, "mode": "set"})
        p = c.get("/api/v1/portfolio").json()
        assert p["cash_ils"] == 2500.0
        assert p["nav_ils"] == 12500.0             # cash is part of the book
        assert p["invested_ils"] == 12500.0        # cash cost basis == face value
        assert p["gain_ils"] == 0.0


def test_mix_always_reports_a_cash_slice():
    with TestClient(m.app) as c:
        _seed(c)
        mix = c.get("/api/v1/mix").json()
        assert "Cash" in mix["current_allocation"]      # present even at zero
        assert mix["current_allocation"]["Cash"] == 0.0
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 2500, "mode": "set"})
        mix = c.get("/api/v1/mix").json()
        assert abs(mix["current_allocation"]["Cash"] - 0.2) < 0.01   # 2500/12500
        assert mix["cash_ils"] == 2500.0


def test_cash_raises_the_liquidity_score():
    with TestClient(m.app) as c:
        _seed(c)
        before = c.get("/api/v1/health-check").json()["liquidity_score"]
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 5000, "mode": "set"})
        after = c.get("/api/v1/health-check").json()["liquidity_score"]
        assert after > before      # cash scores 100, not the generic 70 default


def test_accepting_a_sell_credits_cash_with_a_sane_cost_basis():
    """Regression: credit_cash stored the full proceeds as the PER-SHARE basis, so
    a sell that raised ₪X reported ₪X² of invested capital once totals were shown."""
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "AAA", "market": "TASE", "asset_class": "Equities", "depth": 3,
             "spot_price": 100, "listing_price": 100, "quantity": 400, "cost_basis": 100,
             "expected_return_pct": 7, "volatility_pct": 14},
            {"ticker": "BBB", "market": "TASE", "asset_class": "Equities", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 10, "cost_basis": 100,
             "expected_return_pct": 5, "volatility_pct": 10},
        ]})
        before = c.get("/api/v1/portfolio").json()
        trim = next((r for r in c.get("/api/v1/recommendations").json()["recommendations"]
                     if (r.get("apply") or {}).get("kind") == "trim"), None)
        if trim is None:
            return                                  # no concentration trim on this book
        assert c.post(f"/api/v1/recommendations/{trim['id']}/accept").status_code == 200
        after = c.get("/api/v1/portfolio").json()
        assert after["cash_ils"] > 0                # proceeds are visible as cash
        # Selling doesn't create invested capital: the total must not balloon.
        assert after["invested_ils"] <= before["invested_ils"] + 1.0
        cash_row = next(p for p in after["positions"] if p["ticker"].upper() == "CASH")
        assert cash_row["invested_ils"] == cash_row["value_ils"]   # basis 1.0 x qty
