"""C16 (owner, 2026-10-24: "ship it as a guard").

PR 137 made the sleeve-drift top-up ask the funder before offering Accept -- but only
when ONE sleeve wants the ticker. When two do, `_fund_action` routes Accept to the
whole-plan funder (`fund_plan`), and that branch never asked: it could still offer an
Accept the funder refuses, the card coming back after every press.

Not observed live (the live book has no ticker two sleeves want; a sleeve shares one
only when it follows a live signal, which tests do not have). So the two-sleeve route
is forced here, and the funder's answer is given, the way PR 137's test does.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from tests.test_a_topup_the_funder_refuses_is_not_a_card import (  # noqa: E402
    PREVIEW_OK, REFUSAL, _drift, _today, _under_target_sleeve)


def _two_sleeves_want_it(monkeypatch, answer):
    import app.services.recommendations as rec
    import app.services.strategy_service as ss
    monkeypatch.setattr(rec, "_fund_action", lambda ids: {"kind": "fund_plan"} if ids else {"kind": "none"})
    real = ss.fund_plan
    calls = []

    async def fake(session, user, *a, **kw):
        if kw.get("dry_run"):
            calls.append(kw)
            return dict(answer)
        return await real(session, user, *a, **kw)
    monkeypatch.setattr(ss, "fund_plan", fake)
    return calls


def test_a_whole_plan_top_up_the_funder_refuses_is_a_headsup(monkeypatch):
    calls = _two_sleeves_want_it(monkeypatch, REFUSAL)
    with TestClient(m.app) as c:
        _under_target_sleeve(c)
        r = c.get("/api/v1/recommendations").json()
    assert calls and calls[0].get("honour_holds") is True, "previewed with Accept's own flags"
    assert not _drift(_today(r)), "a card still offers Accept on a whole-plan top-up the funder refuses"
    heads = [h for h in (r.get("headsups") or []) if "has drifted to" in h["title"] and h.get("ticker") == "SOXL"]
    assert heads and (heads[0]["card"].get("apply") or {}).get("kind") == "none"
    assert "off target by 1.2 -> 9.6" in heads[0]["card"]["why"]


def test_a_whole_plan_top_up_the_funder_would_do_keeps_its_accept(monkeypatch):
    _two_sleeves_want_it(monkeypatch, PREVIEW_OK)
    with TestClient(m.app) as c:
        _under_target_sleeve(c)
        r = c.get("/api/v1/recommendations").json()
    cards = _drift(_today(r))
    assert cards and cards[0]["apply"]["kind"] == "fund_plan"
