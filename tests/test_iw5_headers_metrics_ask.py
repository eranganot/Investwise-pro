"""IW-5 (2026-11-05): HSTS + frame-ancestors on every response, /metrics no
longer public, the agent key compared in constant time, /ask bounded."""
from fastapi.testclient import TestClient

import app.main as m
from app.core.config import get_settings


def test_hsts_and_frame_ancestors_on_every_response():
    with TestClient(m.app) as c:
        for path in ("/health", "/login"):
            h = c.get(path).headers
            assert h["strict-transport-security"].startswith("max-age="), path
            assert h["content-security-policy"] == "frame-ancestors 'self'", path
            assert h["x-frame-options"] == "SAMEORIGIN", path


def test_metrics_needs_a_principal_when_auth_is_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "require_auth", True)
    monkeypatch.setattr(get_settings(), "agent_api_key", "agent-key-for-tests-0123456789")
    with TestClient(m.app) as c:
        assert c.get("/metrics").status_code == 401
        r = c.get("/metrics", headers={"x-agent-key": "agent-key-for-tests-0123456789"})
        assert r.status_code == 200 and "http_requests_total" in r.text
        assert c.get("/metrics", headers={"x-agent-key": "wrong"}).status_code == 401


def test_ask_refuses_an_unbounded_question():
    with TestClient(m.app) as c:
        r = c.post("/api/v1/ask", json={"question": "x" * 2001})
        assert r.status_code == 422
