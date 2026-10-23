"""PR 142 -- Ignoring a fired stop does not switch it off (G4 of the 2026-10-22 review).

Ignore on a fired rule card is a dismissal. The next evaluation
(`heal_dismissed_rules`) resolves the rule as "acknowledged", and `resolve_rule`
sets `active = False` on every one-shot type -- stop-loss, trailing stop and
profit-protect included. The position is still held (Ignore sells nothing), so
the book is left with no stop at all, and no card says so.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from tests.test_a_stop_out_is_not_bought_back import _pos  # noqa: E402


def _set_price(tk, px):
    import asyncio

    async def go():
        from sqlalchemy import update
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.config import get_settings
        from app.models.tables import Position
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with eng.begin() as conn:
                await conn.execute(update(Position).where(Position.ticker == tk).values(current_price=px))
        finally:
            await eng.dispose()
    asyncio.run(go())


def _fired(c, rule_type="stop_loss", level=110.0, mode="price", drop_to=None):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("VTI", 60.0), _pos("BND", 60.0, cls="Fixed Income"), _pos("SOXL", 20.0)]})
    c.post("/api/v1/recommendations/restore")
    c.post("/api/v1/recommendations/restore-completed")
    r = c.post("/api/v1/rules", json={"ticker": "SOXL", "rule_type": rule_type, "mode": mode, "level": level})
    assert r.status_code == 200, r.text
    rid = r.json().get("id") or r.json().get("rule", {}).get("id")
    c.post("/api/v1/rules/evaluate")
    if drop_to is not None:            # a trail needs a peak first, then a fall
        _set_price("SOXL", drop_to)
        c.post("/api/v1/rules/evaluate")
    from tests._cards import all_cards
    cards = [x for x in all_cards(c.get("/api/v1/recommendations").json())
             if x["id"] == f"rule_{str(rid)[:8]}"]
    assert cards, "the fired rule must be a card"
    return str(rid), cards[0]


def _rule(c, rid):
    return next(r for r in c.get("/api/v1/rules").json()["rules"] if str(r["id"]) == rid)


def test_ignoring_a_fired_stop_leaves_it_armed():
    with TestClient(m.app) as c:
        rid, card = _fired(c)
        assert "Ignore keeps the position and the stop: it re-arms 10% below today's price." in card["how"]
        assert c.post(f"/api/v1/recommendations/{card['id']}/dismiss").status_code == 200
        c.post("/api/v1/rules/evaluate")          # the heal runs here
        r = _rule(c, rid)
        assert r["active"] is True, f"Ignore switched the stop off: {r}"
        # re-armed one step below today's 100, so it is not breached any more
        assert r["level"] == pytest.approx(90.0) and not r["triggered"] and not r["breached_now"], r
        assert "Ignored" in r["note"] and "110 to 90" in r["note"], r["note"]
        ev = [e for e in c.get("/api/v1/rules/events?limit=50").json()["events"] if str(e["rule_id"]) == rid]
        assert [(e["outcome"], (e["action"] or {}).get("executed", {}).get("by")) for e in ev] == [("kept", "ignore")]
        # and Today does not report a stop that is off
        from tests._cards import all_cards
        t = c.get("/api/v1/recommendations").json()
        assert not [x for x in all_cards(t) if "stop is switched off" in (x.get("title") or "")]
        assert not [h for h in t.get("headsups") or [] if "switched off" in (h.get("title") or "")]


def test_ignoring_a_fired_trailing_stop_restarts_its_trail_from_today():
    with TestClient(m.app) as c:
        rid, card = _fired(c, "trailing_stop", level=5.0, mode="pct", drop_to=90.0)
        c.post(f"/api/v1/recommendations/{card['id']}/dismiss")
        c.post("/api/v1/rules/evaluate")
        r = _rule(c, rid)
        assert r["active"] is True and not r["triggered"], r
        assert r["level"] == 5.0                       # the trail width is unchanged
        assert "trail restarts from today's 90.00" in r["note"], r["note"]


def test_a_stop_that_fires_again_after_the_ignore_is_a_new_card():
    """Re-armed, not muted for good: a fall through the new level fires again."""
    with TestClient(m.app) as c:
        rid, card = _fired(c)
        c.post(f"/api/v1/recommendations/{card['id']}/dismiss")
        c.post("/api/v1/rules/evaluate")
        _set_price("SOXL", 80.0)
        c.post("/api/v1/rules/evaluate")
        r = _rule(c, rid)
        assert r["triggered"] and r["active"], r
        from tests._cards import all_cards
        assert [x for x in all_cards(c.get("/api/v1/recommendations").json()) if x["id"] == card["id"]], \
            "the new firing must un-mute the card"

