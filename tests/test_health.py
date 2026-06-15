"""Health, readiness, and security-header tests (Phase 10)."""
from fastapi.testclient import TestClient

from app.main import app


def test_health_live():
    with TestClient(app) as c:
        assert c.get("/health").json()["status"] == "ok"


def test_health_ready_checks_db():
    with TestClient(app) as c:
        r = c.get("/health/ready")
        assert r.status_code == 200
        assert r.json()["status"] == "ready"


def test_security_headers_present():
    with TestClient(app) as c:
        h = c.get("/health").headers
        assert h.get("x-content-type-options") == "nosniff"
        assert h.get("x-frame-options") == "SAMEORIGIN"
