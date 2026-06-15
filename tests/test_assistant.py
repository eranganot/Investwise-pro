"""Phase H - grounded Q&A + weekly digest."""
from fastapi.testclient import TestClient

import app.services.ask_service as ask_mod
import app.services.digest_service as digest_mod
from app.main import app

PORT = {"entity_name": "Personal", "positions": [
    {"ticker": "AAA", "market": "NYSE", "asset_class": "Equities", "depth": 2,
     "spot_price": 1, "listing_price": 1, "quantity": 100, "cost_basis": 50}]}


def test_gemini_off_returns_none(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from app.services.llm import gemini_generate
    assert gemini_generate("hi") is None


def test_ask_without_key_returns_grounded_context():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        r = c.post("/api/v1/ask", json={"question": "How am I doing?"}).json()
        assert r["llm"] is False
        assert "nav_ils" in r["grounded_on"] and "holdings" in r["grounded_on"]
        assert r["context"]["nav_ils"] >= 0


def test_ask_with_stubbed_llm_answers(monkeypatch):
    monkeypatch.setattr(ask_mod, "gemini_enabled", lambda: True)
    monkeypatch.setattr(ask_mod, "gemini_generate",
                        lambda prompt, **k: "Your biggest position is AAA. Not financial advice.")
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        r = c.post("/api/v1/ask", json={"question": "What's my biggest holding?"}).json()
        assert r["llm"] is True and "AAA" in r["answer"]


def test_digest_fallback_without_key_has_numbers():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        c.post("/api/v1/portfolio/refresh-prices")
        r = c.get("/api/v1/digest").json()
        assert r["llm"] is False
        assert "Net worth tracked" in r["digest"] and "Not financial advice" in r["digest"]


def test_digest_uses_llm_when_available(monkeypatch):
    monkeypatch.setattr(digest_mod, "gemini_enabled", lambda: True)
    monkeypatch.setattr(digest_mod, "gemini_generate", lambda prompt, **k: "You're on track. Not financial advice.")
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        r = c.get("/api/v1/digest").json()
        assert r["llm"] is True and "on track" in r["digest"]
