"""C18 (owner, 2026-10-23): switched-off protection comes back on by itself after
`rearm_after_days` (3), never one already crossed.

On 10-22 about twenty stops were found off only when one that should have fired
stayed silent. PR 140 listed them on a heads-up with "Re-arm all"; nothing re-armed
one that the owner forgot.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import asyncio  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from tests.test_a_stop_out_is_not_bought_back import _pos  # noqa: E402


def _db(fn):
    async def go():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.config import get_settings
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as s:
                return await fn(s)
        finally:
            await eng.dispose()
    return asyncio.run(go())


def _paused_days_ago(rid, days):
    """Move the rule's pause event (written by toggle_rule) into the past."""
    async def fn(s):
        from sqlalchemy import update

        from app.models.tables import RuleEvent
        await s.execute(update(RuleEvent).where(RuleEvent.rule_id == rid, RuleEvent.outcome == "paused")
                        .values(outcome_at=datetime.now(timezone.utc) - timedelta(days=days)))
        await s.commit()
    _db(fn)


def _rule(c, rid):
    return next(r for r in c.get("/api/v1/rules").json()["rules"] if str(r["id"]) == rid)


def _book(c, rules):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("VTI", 60.0), _pos("SOXL", 20.0)]})
    ids = []
    for tk, rt, mode, lvl in rules:
        r = c.post("/api/v1/rules", json={"ticker": tk, "rule_type": rt, "mode": mode, "level": lvl})
        assert r.status_code == 200, r.text
        rid = str(r.json()["id"])
        assert c.post(f"/api/v1/rules/{rid}/toggle").status_code == 200        # switched off
        ids.append(rid)
    return ids


def test_a_stop_off_for_longer_than_the_setting_comes_back_on():
    with TestClient(m.app) as c:
        old, recent = _book(c, [("VTI", "stop_loss", "price", 80.0), ("SOXL", "stop_loss", "price", 70.0)])
        _paused_days_ago(old, 4)
        _paused_days_ago(recent, 1)
        c.post("/api/v1/rules/evaluate")
        o, r = _rule(c, old), _rule(c, recent)
        assert o["active"] is True and not o["triggered"], o
        assert "Re-armed automatically" in o["note"]
        assert r["active"] is False, "one day off is inside the 3-day grace"
        ev = [e for e in c.get("/api/v1/rules/events?limit=50").json()["events"] if str(e["rule_id"]) == old]
        assert ("resumed", "auto") in [(e["outcome"], (e["action"] or {}).get("by")) for e in ev]


def test_a_crossed_stop_is_never_re_armed_into_a_sale():
    with TestClient(m.app) as c:
        [rid] = _book(c, [("VTI", "stop_loss", "price", 120.0)])          # price 100 < 120: crossed
        _paused_days_ago(rid, 10)
        c.post("/api/v1/rules/evaluate")
        assert _rule(c, rid)["active"] is False


def test_a_trailing_stop_restarts_its_trail_from_today():
    with TestClient(m.app) as c:
        [rid] = _book(c, [("VTI", "trailing_stop", "pct", 10.0)])

        async def high_peak(s):                                            # paused after a fall
            import uuid

            from app.models.tables import TradingRule
            r = await s.get(TradingRule, uuid.UUID(rid))
            r.peak_price = 150.0
            await s.commit()
        _db(high_peak)
        _paused_days_ago(rid, 5)
        c.post("/api/v1/rules/evaluate")
        r = _rule(c, rid)
        assert r["active"] is True and not r["breached_now"], r


def test_zero_days_means_never():
    from app.services import thresholds
    with TestClient(m.app) as c:
        [rid] = _book(c, [("VTI", "stop_loss", "price", 80.0)])
        _paused_days_ago(rid, 30)
        tok = thresholds._OVERRIDES.set({"rearm_after_days": 0.0})
        try:
            async def run(s):
                from app.services.feed_service import ensure_superadmin
                from app.services.rules_service import _positions_index_and_nav, auto_rearm
                u = await ensure_superadmin(s)
                idx, nav = await _positions_index_and_nav(s, u)
                return await auto_rearm(s, u, idx, nav)
            assert _db(run) == []
        finally:
            thresholds._OVERRIDES.reset(tok)
        assert _rule(c, rid)["active"] is False


def test_the_setting_is_on_the_card_thresholds_sheet():
    from app.services import thresholds
    assert "rearm_after_days" in thresholds.SPEC
    assert thresholds.defaults()["rearm_after_days"] == 3.0
