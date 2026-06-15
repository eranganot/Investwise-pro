"""Agent override key + remember-me session (Phase: admin access)."""
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


def test_agent_key_writes_even_with_auth_on(monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "true")
    monkeypatch.setenv("ALLOWED_EMAILS", "eran.ganot@gmail.com")
    monkeypatch.setenv("AGENT_API_KEY", "testkey123")
    get_settings.cache_clear()
    try:
        with TestClient(app) as c:
            assert c.get("/api/v1/auth/me").status_code == 401            # no creds
            h = {"X-Agent-Key": "testkey123"}
            me = c.get("/api/v1/auth/me", headers=h).json()
            assert me["role"] == "SUPERADMIN" and me["sub"] == "eran.ganot@gmail.com"
            r = c.post("/api/v1/intake/portfolio", headers=h, json={"entity_name": "Personal",
                "positions": [{"ticker": "AAA", "market": "NYSE", "depth": 1,
                               "spot_price": 1, "listing_price": 1, "quantity": 1, "cost_basis": 1}]})
            assert r.status_code == 200
    finally:
        get_settings.cache_clear()


def test_wrong_agent_key_is_rejected(monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "true")
    monkeypatch.setenv("AGENT_API_KEY", "testkey123")
    get_settings.cache_clear()
    try:
        with TestClient(app) as c:
            assert c.get("/api/v1/auth/me", headers={"X-Agent-Key": "wrong"}).status_code == 401
    finally:
        get_settings.cache_clear()


def test_remember_me_default_is_30_days():
    assert get_settings().session_ttl_sec == 2592000


def test_jwt_secret_makes_sessions_survive_restart(monkeypatch):
    import app.core.auth as A
    from app.core.auth import Role, create_token, decode_token
    monkeypatch.setenv("JWT_SECRET", "stable-secret-123")
    from app.core.config import get_settings
    get_settings.cache_clear()
    try:
        tok = create_token("eran.ganot@gmail.com", Role.SUPERADMIN, "access")
        A._keys.cache_clear()  # simulate a redeploy rotating the ephemeral RSA key
        payload = decode_token(tok)  # must still verify (HS256 stable secret)
        assert payload["sub"] == "eran.ganot@gmail.com" and payload["role"] == "SUPERADMIN"
    finally:
        get_settings.cache_clear()
