"""Phase 0: portfolio totals (invested / gain / cash) + reversible dismissals.

The Total-portfolio-value card showed only NAV, so "am I actually up?" was
unanswerable in the app; and an "Ignore" was a one-way door for 7 days, which
made a deliberately-emptied Today look identical to a genuinely healthy one.
"""
import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
from fastapi.testclient import TestClient
import app.main as m


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "TEVA", "market": "TASE", "asset_class": "Equities", "depth": 3,
         "spot_price": 120, "listing_price": 120, "quantity": 100, "cost_basis": 100,
         "expected_return_pct": 7, "volatility_pct": 14},
    ]})


def test_portfolio_reports_invested_gain_and_pct():
    with TestClient(m.app) as c:
        _seed(c)
        p = c.get("/api/v1/portfolio").json()
        # ILS-native holding: 100 shares, paid 100, now worth 120
        assert p["invested_ils"] == 10000.0
        assert p["nav_ils"] == 12000.0
        assert p["gain_ils"] == 2000.0
        assert p["gain_pct"] == 20.0
        pos = p["positions"][0]
        assert pos["invested_ils"] == 10000.0 and pos["gain_ils"] == 2000.0


def test_gain_pct_is_none_when_nothing_invested():
    with TestClient(m.app) as c:
        p = c.get("/api/v1/portfolio").json()
        assert p["invested_ils"] == 0.0
        assert p["gain_pct"] is None      # no divide-by-zero


def test_cash_total_is_reported_separately():
    with TestClient(m.app) as c:
        _seed(c)
        assert c.get("/api/v1/portfolio").json()["cash_ils"] == 0.0
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "CASH", "market": "TASE", "asset_class": "Cash", "depth": 1,
             "spot_price": 1, "listing_price": 1, "quantity": 2500, "cost_basis": 1},
        ]})
        assert c.get("/api/v1/portfolio").json()["cash_ils"] == 2500.0


def test_dismissed_recommendations_are_counted_and_restorable():
    with TestClient(m.app) as c:
        _seed(c)
        recs = c.get("/api/v1/recommendations").json()
        assert "degraded" in recs and "dismissed_count" in recs
        ids = [r["id"] for r in recs["recommendations"]]
        if not ids:
            return                                    # nothing to dismiss on this book
        assert c.post(f"/api/v1/recommendations/{ids[0]}/dismiss").status_code == 200
        after = c.get("/api/v1/recommendations").json()
        assert ids[0] not in [r["id"] for r in after["recommendations"]]
        assert after["dismissed_count"] >= 1          # hidden, and the app knows it
        r = c.post("/api/v1/recommendations/restore").json()
        assert r["ok"] and r["restored"] >= 1
        back = c.get("/api/v1/recommendations").json()
        assert ids[0] in [x["id"] for x in back["recommendations"]]
        assert back["dismissed_count"] == 0
