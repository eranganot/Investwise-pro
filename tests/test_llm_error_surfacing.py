"""An AI failure must say WHY, not just "unavailable".

Reported live: the What's Moving panel showed "Summary unavailable." and Ask
InvestWise showed "The assistant couldn't reach the model just now". The actual
cause, found only via /api/v1/adversary/diagnostics, was:

    HTTP 429: "Your prepayment credits are depleted."

A two-minute billing top-up rendered identically to a permanent outage, because
gemini_generate collapsed every failure mode into a bare None.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.services import llm  # noqa: E402


def test_quota_exhaustion_is_actionable():
    msg = llm._classify(RuntimeError('HTTP 429: {"error": {"code": 429, '
                                     '"message": "Your prepayment credits are depleted."}}'), "k")
    assert "credit" in msg.lower() or "billing" in msg.lower()
    assert "unavailable" not in msg.lower(), "must not read as a permanent outage"


def test_each_failure_mode_is_distinguishable():
    seen = {
        llm._classify(None, None),                                  # no key
        llm._classify(RuntimeError("HTTP 429: quota"), "k"),        # billing
        llm._classify(RuntimeError("HTTP 403: bad API key"), "k"),  # bad key
        llm._classify(RuntimeError("HTTP 404: model not found"), "k"),
        llm._classify(RuntimeError("request timed out"), "k"),
        llm._classify(RuntimeError("connection reset"), "k"),       # generic
    }
    assert len(seen) == 6, f"failure modes collapsed into: {seen}"


def test_missing_key_says_so():
    assert "not configured" in llm._classify(None, None).lower()


def test_generate_ex_returns_a_reason_and_never_raises(monkeypatch):
    monkeypatch.setattr(llm, "gemini_key", lambda: "fake-key")
    def _boom(*_a, **_kw):
        raise RuntimeError('HTTP 429: {"message": "Your prepayment credits are depleted."}')
    monkeypatch.setattr(llm, "_gemini_call", _boom)

    text, err = llm.gemini_generate_ex("hello")
    assert text is None
    assert err and ("credit" in err.lower() or "billing" in err.lower())
    # The old signature still works for callers that only want the text.
    assert llm.gemini_generate("hello") is None
