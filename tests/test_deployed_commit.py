"""/health reports the commit that is actually serving the request.

Not cosmetic. Every ship script tells you to confirm Railway shows the right
SHA "BEFORE believing any smoke result", and until now that could only be done
by eye in the dashboard. STATUS records two debugging rounds lost to smoking a
stale container, and Phase B's own verification hit it a third time -- the
after-measurement was untrustworthy until a screenshot confirmed the deploy.
"""
from __future__ import annotations

from app.api.routes.health import deployed_commit


def test_reports_the_railway_commit(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "cd2cc8b1234567890abcdef")
    assert deployed_commit() == "cd2cc8b"


def test_falls_back_to_other_providers(monkeypatch):
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.setenv("GIT_COMMIT_SHA", "abc1234def")
    assert deployed_commit() == "abc1234"


def test_says_unknown_rather_than_guessing(monkeypatch):
    """The failure mode being fixed is a number that LOOKS like the deployed
    commit but is not. app_version ("22.1") is hand-edited and would be exactly
    that, so absence must read as absence."""
    for var in ("RAILWAY_GIT_COMMIT_SHA", "GIT_COMMIT_SHA", "SOURCE_COMMIT"):
        monkeypatch.delenv(var, raising=False)
    assert deployed_commit() == "unknown"


def test_health_payload_carries_it(monkeypatch):
    import asyncio

    from app.api.routes.health import health

    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "deadbeefcafe")
    payload = asyncio.run(health())
    assert payload["commit"] == "deadbee"
    # version stays -- it answers a different question, and conflating the two
    # is what made this necessary.
    assert "version" in payload
