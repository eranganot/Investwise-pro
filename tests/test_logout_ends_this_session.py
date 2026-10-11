"""PR 111 (D73, D-f option 2): logging out ends THIS browser's session.

S2, proven 2026-10-08: after `/auth/logout` the same bearer and a replayed
cookie both still returned 200 -- logout only deleted the cookie, and the
30-day SUPERADMIN token inside it stayed valid to anyone holding a copy.
Owner's choice: only the browser you log out of is signed out.
"""
import pytest
from fastapi.testclient import TestClient

import app.main as m
from app.core import auth as A
from app.core.auth import Role, create_token
from app.core.config import get_settings

OWNER = "eran.ganot@gmail.com"


@pytest.fixture
def enforced(monkeypatch):
    from app.api import deps
    s = get_settings().model_copy(update={"require_auth": True, "allowed_emails": OWNER,
                                          "agent_api_key": "agent-secret-for-tests"})
    monkeypatch.setattr(A, "get_settings", lambda: s)
    monkeypatch.setattr(deps, "get_settings", lambda: s)
    getattr(A, "_REVOKED_CACHE", {}).clear()
    return s


def _token():
    return create_token(OWNER, Role.SUPERADMIN, "access", ttl=3600)


def _cookie(s, tok):
    """The session cookie as an explicit header: per-request cookie dicts are
    deprecated in Starlette, and a client-side jar could mask a replay."""
    return {"Cookie": f"{s.session_cookie_name}={tok}"}


def test_a_logged_out_cookie_cannot_be_replayed(enforced):
    tok = _token()
    cookie = _cookie(enforced, tok)
    with TestClient(m.app) as c:
        assert c.get("/api/v1/portfolio", headers=cookie).status_code == 200
        r = c.get("/auth/logout", headers=cookie, follow_redirects=False)
        assert r.status_code in (302, 307)
        # On HEAD: 200 -- the token outlived the logout.
        assert c.get("/api/v1/portfolio", headers=cookie).status_code == 401
        assert c.get("/api/v1/portfolio", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_logout_survives_a_cold_cache(enforced):
    """The revocation is in the database, not just this process's memory."""
    tok = _token()
    cookie = _cookie(enforced, tok)
    with TestClient(m.app) as c:
        c.get("/auth/logout", headers=cookie, follow_redirects=False)
        getattr(A, "_REVOKED_CACHE", {}).clear()                     # e.g. a restart
        assert c.get("/api/v1/portfolio", headers=cookie).status_code == 401


def test_other_devices_stay_signed_in_and_a_new_login_works(enforced):
    phone, laptop = _token(), _token()
    with TestClient(m.app) as c:
        c.get("/auth/logout", headers=_cookie(enforced, laptop), follow_redirects=False)
        # D-f option 2: logging out on the laptop leaves the phone signed in.
        assert c.get("/api/v1/portfolio", headers=_cookie(enforced, phone)).status_code == 200
        fresh = _token()
        assert c.get("/api/v1/portfolio", headers=_cookie(enforced, fresh)).status_code == 200
        assert c.get("/api/v1/portfolio", headers={"x-agent-key": "agent-secret-for-tests"}).status_code == 200


def test_a_logout_without_a_session_is_harmless(enforced):
    with TestClient(m.app) as c:
        r = c.get("/auth/logout", headers=_cookie(enforced, "not-a-jwt"), follow_redirects=False)
        assert r.status_code in (302, 307)
