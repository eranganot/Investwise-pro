"""L15 scratch: a billing 429 on grounded research reaches the UI as a generic outage."""
import io
import os
import urllib.error
import sys
sys.path.insert(0, "C:/dev/Investwise-pro/.claude/worktrees/docs-overhaul")

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ["GOOGLE_API_KEY"] = "fake"

from app.services import llm  # noqa: E402

calls = []


def _raise_429(req, timeout=None):
    calls.append(req.full_url)
    raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {},
                                 io.BytesIO(b'{"error":{"message":"Your prepayment credits are depleted."}}'))


def test_grounded_research_429_is_reported_as_generic(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen", _raise_429)
    assert llm.gemini_generate_grounded("x") is None  # no reason travels
    print("grounded calls on a 429:", len(calls))
    assert len(calls) == 2  # grounded attempt, then an ungrounded retry of a billing error


def test_research_endpoint_text(monkeypatch):
    monkeypatch.setattr(llm, "gemini_generate_grounded", lambda *a, **k: None)
    from app.services import ai_service
    import inspect
    src = inspect.getsource(ai_service)
    assert "Research is temporarily unavailable" in src
