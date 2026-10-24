"""D-1 (owner, 2026-10-24): the daily production smoke runs unattended, so its key must not
be the agent key (which acts as the owner and can accept cards, change rules, sell). A second
key, `smoke_api_key`, is accepted for GET/HEAD only; anything else is refused 403.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.core.auth as A  # noqa: E402
import app.main as m  # noqa: E402
from app.core.config import get_settings  # noqa: E402

SMOKE, AGENT = "smoke-" + "x" * 40, "agent-" + "y" * 40


@pytest.fixture
def keys(monkeypatch):
    from app.api import deps
    s = get_settings().model_copy(update={"require_auth": True, "allowed_emails": "",
                                          "agent_api_key": AGENT, "smoke_api_key": SMOKE})
    monkeypatch.setattr(A, "get_settings", lambda: s)
    monkeypatch.setattr(deps, "get_settings", lambda: s)
    return s


def test_the_smoke_key_reads_the_owners_book(keys):
    with TestClient(m.app) as c:
        for path in ("/api/v1/recommendations", "/api/v1/plan/optimizer", "/api/v1/rules", "/api/v1/portfolio"):
            assert c.get(path, headers={"x-agent-key": SMOKE}).status_code == 200, path


def test_the_smoke_key_cannot_write_anything(keys):
    with TestClient(m.app) as c:
        for meth, path in (("POST", "/api/v1/recommendations/rec_x/accept"),
                           ("POST", "/api/v1/rules/evaluate"),
                           ("PUT", "/api/v1/plan"),
                           ("DELETE", "/api/v1/portfolio/position?ticker=X&market=NASDAQ"),
                           ("POST", "/api/v1/plan/optimizer/refresh")):
            r = c.request(meth, path, headers={"x-agent-key": SMOKE}, json={})
            assert r.status_code == 403 and "read-only" in r.text, (meth, path, r.status_code)


def test_the_agent_key_still_writes_and_a_wrong_key_is_nobody(keys):
    with TestClient(m.app) as c:
        assert c.post("/api/v1/rules/evaluate", headers={"x-agent-key": AGENT}).status_code != 403
        assert c.get("/api/v1/portfolio", headers={"x-agent-key": SMOKE + "z"}).status_code == 401
        assert c.get("/api/v1/portfolio").status_code == 401


def test_a_smoke_key_equal_to_the_agent_key_is_not_a_read_only_key(monkeypatch):
    """Set to the same value by mistake, it must not turn the agent key read-only (nor the reverse)."""
    from app.api import deps
    s = get_settings().model_copy(update={"require_auth": True, "allowed_emails": "",
                                          "agent_api_key": AGENT, "smoke_api_key": AGENT})
    monkeypatch.setattr(A, "get_settings", lambda: s)
    monkeypatch.setattr(deps, "get_settings", lambda: s)
    with TestClient(m.app) as c:
        assert c.post("/api/v1/rules/evaluate", headers={"x-agent-key": AGENT}).status_code != 403
