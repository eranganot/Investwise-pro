"""API-level tests for Wave B (weekly-from-portfolio, expiry) - via TestClient."""
from fastapi.testclient import TestClient

from app.main import app

PERSONAL = {"entity_name": "Personal", "positions": [
    {"ticker": "AAPL", "market": "NYSE", "depth": 3, "spot_price": 100, "listing_price": 107,
     "quantity": 10, "cost_basis": 80, "expected_return_pct": 9, "volatility_pct": 14}]}


def test_weekly_feed_uses_intaken_portfolio():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PERSONAL)
        wf = c.get("/api/v1/decision-feed/weekly").json()
        assert wf["count"] <= 10
        assert "AAPL" in [i["ticker"] for i in wf["items"]]


def test_latest_items_carry_expiry():
    with TestClient(app) as c:
        c.post("/api/v1/decision-feed/generate", json={})
        latest = c.get("/api/v1/decision-feed/latest").json()
        live = [i for i in latest["items"] if not i["veto_flag"]]
        assert live and live[0]["expires_at"] is not None
        assert live[0]["stale"] is False


def test_auth_status_reports_require_auth():
    with TestClient(app) as c:
        assert c.get("/api/v1/auth/status").json() == {"auth_enabled": False}
