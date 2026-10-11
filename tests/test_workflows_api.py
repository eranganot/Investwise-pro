"""API-level tests for Wave B (weekly-from-portfolio, expiry) - via TestClient."""
from fastapi.testclient import TestClient

from app.main import app

PERSONAL = {"entity_name": "Personal", "positions": [
    {"ticker": "AAPL", "market": "NYSE", "depth": 3, "spot_price": 100, "listing_price": 107,
     "quantity": 10, "cost_basis": 80, "expected_return_pct": 9, "volatility_pct": 14}]}


def test_auth_status_reports_require_auth():
    with TestClient(app) as c:
        assert c.get("/api/v1/auth/status").json() == {"auth_enabled": False}
