"""Phase A - Google sign-in, cookie sessions, email allowlist."""
import pytest
from fastapi.testclient import TestClient

import app.api.routes.google_auth as ga
from app.core.auth import Role, create_token
from app.core.config import get_settings
from app.main import app

ALLOWED = "eran.ganot@gmail.com"


@pytest.fixture
def auth_env(monkeypatch):
    """Flip auth-related settings for a test, then reset the cached Settings."""
    def _set(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        return get_settings()
    yield _set
    get_settings.cache_clear()


def test_login_page_renders_google_button():
    with TestClient(app) as c:
        r = c.get("/login")
        assert r.status_code == 200 and "Sign in with Google" in r.text


def test_protected_endpoint_401_without_session(auth_env):
    auth_env(REQUIRE_AUTH="true", ALLOWED_EMAILS=ALLOWED)
    with TestClient(app) as c:
        assert c.get("/api/v1/auth/me").status_code == 401


def test_allow_listed_cookie_grants_access(auth_env):
    s = auth_env(REQUIRE_AUTH="true", ALLOWED_EMAILS=ALLOWED)
    token = create_token(ALLOWED, Role.SUPERADMIN, "access")
    with TestClient(app, cookies={s.session_cookie_name: token}) as c:
        r = c.get("/api/v1/auth/me")
        assert r.status_code == 200 and r.json()["sub"] == ALLOWED


def test_non_allow_listed_cookie_is_forbidden(auth_env):
    s = auth_env(REQUIRE_AUTH="true", ALLOWED_EMAILS=ALLOWED)
    token = create_token("stranger@gmail.com", Role.SUPERADMIN, "access")
    with TestClient(app, cookies={s.session_cookie_name: token}) as c:
        assert c.get("/api/v1/auth/me").status_code == 403


def test_google_login_redirects_to_google(auth_env):
    auth_env(REQUIRE_AUTH="true", ALLOWED_EMAILS=ALLOWED,
             GOOGLE_OAUTH_CLIENT_ID="cid", GOOGLE_OAUTH_CLIENT_SECRET="secret")
    with TestClient(app) as c:
        r = c.get("/auth/google/login", follow_redirects=False)
        assert r.status_code in (302, 307)
        assert "accounts.google.com" in r.headers["location"]
        assert "iw_oauth_state" in r.headers.get("set-cookie", "")


def test_callback_state_mismatch_is_rejected(auth_env):
    auth_env(REQUIRE_AUTH="true", ALLOWED_EMAILS=ALLOWED,
             GOOGLE_OAUTH_CLIENT_ID="cid", GOOGLE_OAUTH_CLIENT_SECRET="secret")
    with TestClient(app) as c:
        r = c.get("/auth/google/callback?code=x&state=wrong", follow_redirects=False)
        assert r.status_code in (302, 307) and "/login" in r.headers["location"]


def _stub_google(email, verified=True):
    def _fake(req, timeout=15.0):
        url = req.full_url
        if "token" in url:
            return {"access_token": "fake-access"}
        return {"email": email, "email_verified": verified, "name": "Test"}
    return _fake


def test_callback_success_sets_session_for_allowed_email(auth_env, monkeypatch):
    s = auth_env(REQUIRE_AUTH="true", ALLOWED_EMAILS=ALLOWED,
                 GOOGLE_OAUTH_CLIENT_ID="cid", GOOGLE_OAUTH_CLIENT_SECRET="secret")
    monkeypatch.setattr(ga, "_http_json", _stub_google(ALLOWED))
    with TestClient(app, cookies={"iw_oauth_state": "st"}) as c:
        r = c.get("/auth/google/callback?code=good&state=st", follow_redirects=False)
        assert r.status_code in (302, 307) and r.headers["location"] == s.post_login_redirect
        assert s.session_cookie_name in r.headers.get("set-cookie", "")


def test_callback_rejects_non_allow_listed_email(auth_env, monkeypatch):
    auth_env(REQUIRE_AUTH="true", ALLOWED_EMAILS=ALLOWED,
             GOOGLE_OAUTH_CLIENT_ID="cid", GOOGLE_OAUTH_CLIENT_SECRET="secret")
    monkeypatch.setattr(ga, "_http_json", _stub_google("intruder@gmail.com"))
    with TestClient(app, cookies={"iw_oauth_state": "st"}) as c:
        r = c.get("/auth/google/callback?code=good&state=st", follow_redirects=False)
        assert "/login" in r.headers["location"] and "not+allowed" in r.headers["location"]
