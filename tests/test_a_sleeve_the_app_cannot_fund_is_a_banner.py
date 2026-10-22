"""2026-10-22 -- "if I can't do that, I want a bold and strong notification that I
can't act on this sleeve and I need to drop this sleeve."

PR 137 turned a top-up the funder refuses into a one-line Heads-up. That is honest
but quiet: the card that had been on Today for days became a line in a collapsed
list. The user has to be told, loudly, that this sleeve cannot be funded and what
he can do about it.

`payload["blockers"]` carries it. It is built only from the funder's own preview,
cannot be swiped away with the heads-up, and offers two choices -- open the plan,
or drop the sleeve (DELETE /plan/sleeves/{id}, which sells nothing).
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from tests.test_a_topup_the_funder_refuses_is_not_a_card import (  # noqa: E402
    PREVIEW_OK,
    REFUSAL,
    SOXL,
    _patch_preview,
    _under_target_sleeve,
)


def test_a_refused_top_up_produces_a_blocker(monkeypatch):
    _patch_preview(monkeypatch, REFUSAL)
    with TestClient(m.app) as c:
        _under_target_sleeve(c)
        r = c.get("/api/v1/recommendations").json()

    bl = r.get("blockers")
    assert bl, "a sleeve the funder refuses produced no blocker"
    b = bl[0]
    assert b["kind"] == "sleeve_unfundable" and b["severity"] == "HIGH"
    assert "can't act" in b["title"] and "SOXL" in b["title"]
    assert "off target by 1.2 -> 9.6" in b["reason"], "the funder's own reason is not shown"
    assert b["strategy_id"] == SOXL
    kinds = [x["kind"] for x in b["choices"]]
    assert kinds == ["open_plan", "drop_sleeve"], kinds
    drop = b["choices"][1]
    assert drop["strategy_id"] == SOXL and "Nothing is sold" in drop["confirm"]


def test_the_heads_up_is_still_there_and_the_blocker_is_not_a_card(monkeypatch):
    _patch_preview(monkeypatch, REFUSAL)
    with TestClient(m.app) as c:
        _under_target_sleeve(c)
        r = c.get("/api/v1/recommendations").json()
    assert any("has drifted to" in h["title"] for h in r.get("headsups", []))
    on_today = (r.get("recommendations") or []) + (r.get("more_recommendations") or [])
    assert not [x for x in on_today if "has drifted to" in (x.get("title") or "")
                and (x.get("meta") or {}).get("ticker") == "SOXL"]


def test_a_fundable_top_up_has_no_blocker(monkeypatch):
    _patch_preview(monkeypatch, PREVIEW_OK)
    with TestClient(m.app) as c:
        _under_target_sleeve(c)
        r = c.get("/api/v1/recommendations").json()
    assert r.get("blockers") == [], "a fundable sleeve was reported as blocked"


def test_swiping_the_headsup_away_does_not_silence_the_blocker(monkeypatch):
    _patch_preview(monkeypatch, REFUSAL)
    with TestClient(m.app) as c:
        _under_target_sleeve(c)
        r = c.get("/api/v1/recommendations").json()
        hid = next(h["id"] for h in r["headsups"] if "has drifted to" in h["title"])
        assert c.post(f"/api/v1/recommendations/headsups/{hid}/dismiss").status_code == 200
        again = c.get("/api/v1/recommendations").json()
    assert not [h for h in again["headsups"] if h["id"] == hid], "the heads-up was not dismissed"
    assert again["blockers"], "dismissing the one-liner silenced the blocker"


def test_the_helper_is_quiet_when_there_is_nothing_to_say():
    from app.services.recommendations import sleeve_blockers
    assert sleeve_blockers([]) == []
    assert sleeve_blockers([{"id": "x", "card": {"meta": {"ticker": "V"}}}]) == []
