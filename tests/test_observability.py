"""Observability tests (review M3)."""
from fastapi.testclient import TestClient

from app.main import app


def test_metrics_endpoint_exposes_prometheus():
    with TestClient(app) as c:
        c.get("/health")  # generate a metric
        r = c.get("/metrics")
        assert r.status_code == 200
        assert "http_requests_total" in r.text


def test_request_id_header_present():
    with TestClient(app) as c:
        assert c.get("/health").headers.get("x-request-id")


def test_mutations_are_audited_and_viewable():
    with TestClient(app) as c:
        c.post("/api/v1/safety/check", json={"holdings": {}, "liquidity_ratio": 1.0, "proposals": []})
        audit = c.get("/api/v1/audit").json()
        assert audit["count"] >= 1
        assert any(e["route"].endswith("/safety/check") for e in audit["entries"])
