"""PR 143 -- a rule check that fails says so, and job history survives a restart.

The trading rules are evaluated in ONE place: inside the 30-minute `price_refresh`
job (`pricing_service._refresh_all`), behind

    except Exception:
        logger.warning("rule evaluation failed", exc_info=False)

so a failing rule check left the job `last_ok: true` with no `rules` key, logged
one line without the error, and nothing else anywhere said a stop had not been
checked. And `scheduler.JOB_STATE` lives in memory only: read on production
2026-10-23 11:05 UTC, four minutes after a deploy, every job showed `runs: 1` and
the nightly ones (`nav_snapshot`, `strategy_signals`, ...) no history at all, so
whether last night's 06:15 signal job ran could not be told from the app.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import asyncio  # noqa: E402

import pytest  # noqa: E402


def _refresh_with_a_failing_rule_check(monkeypatch):
    from app.services import pricing_service as ps
    from app.services import rules_service as rs

    async def no_prices(session):
        return {"updated": 0, "failed": 0}

    async def boom(session):
        raise RuntimeError("the rule check broke")
    monkeypatch.setattr(ps, "refresh_all_positions", no_prices)
    monkeypatch.setattr(rs, "evaluate_all", boom)
    return ps


def test_a_failed_rule_check_fails_the_job(monkeypatch):
    ps = _refresh_with_a_failing_rule_check(monkeypatch)
    with pytest.raises(RuntimeError, match="rule check"):
        ps.run_price_refresh_blocking()


def test_the_failure_is_recorded_with_its_reason(monkeypatch):
    _refresh_with_a_failing_rule_check(monkeypatch)
    from app.services import pricing_service as ps
    from app.worker import scheduler as sch
    monkeypatch.setattr(sch, "JOB_STATE", {})
    sch._guarded("price_refresh", ps.run_price_refresh_blocking)()
    st = sch.job_state()["history"]["price_refresh"]
    assert st["last_ok"] is False, st
    assert "the rule check broke" in st["last_error"], st


def test_one_book_that_fails_does_not_skip_the_others(monkeypatch):
    """evaluate_all looped over subjects with no guard: the first exception ended
    the loop, so every later book went unchecked."""
    from app.services import rules_service as rs

    seen = []

    async def ev(session, user, notify=False):
        seen.append(user.email)
        if user.email.startswith("a-"):
            raise RuntimeError("bad book")
        return []

    async def go():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.config import get_settings
        from app.services.feed_service import ensure_user
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as s:
                from app.services.rules_service import create_rule
                for e in ("a-pr143@example.com", "b-pr143@example.com"):
                    u = await ensure_user(s, e)
                    await create_rule(s, u, ticker="VTI", rule_type="price_below", mode="price", level=1.0)
                await s.commit()
                monkeypatch.setattr(rs, "evaluate_user", ev)
                with pytest.raises(RuntimeError, match="1 of"):
                    await rs.evaluate_all(s)
        finally:
            await eng.dispose()
    asyncio.run(go())
    assert "a-pr143@example.com" in seen and "b-pr143@example.com" in seen, seen


def test_job_history_survives_a_restart(monkeypatch):
    from app.worker import scheduler as sch
    monkeypatch.setattr(sch, "JOB_STATE", {})
    sch._guarded("strategy_signals", lambda: {"checked": 3, "flipped": 0})()
    sch._guarded("nav_snapshot", lambda: (_ for _ in ()).throw(ValueError("no prices")))()
    monkeypatch.setattr(sch, "JOB_STATE", {})              # a deploy: the process is new
    sch.load_persisted_state()
    h = sch.job_state()["history"]
    assert h["strategy_signals"]["last_ok"] is True and h["strategy_signals"]["last_result"] == {"checked": 3, "flipped": 0}
    assert h["nav_snapshot"]["last_ok"] is False and "no prices" in h["nav_snapshot"]["last_error"]
    assert h["strategy_signals"]["last_success"]
    sch._guarded("strategy_signals", lambda: {"checked": 1, "flipped": 0})()   # the next run continues it
    h = sch.job_state()["history"]["strategy_signals"]
    assert h["runs"] == 2 and h["previous_run"] and h["previous_run"] < h["last_run"]


def _state(**st):
    return {"scheduler_running": True, "history": {"price_refresh": st}}


ARMED = [{"id": "s1", "ticker": "VTI", "rule_type": "stop_loss", "active": True}]


def test_a_failed_check_is_a_heads_up_on_today():
    from datetime import datetime, timezone

    from app.services.recommendations import _rule_check_headsup
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    [h] = _rule_check_headsup(_state(last_ok=False, last_success="2026-10-23T11:30:00+00:00",
                                     last_error="RuntimeError: rule check failed"), ARMED, now)
    assert h["title"] == "Your trading rules have not been checked since 23 Oct 11:30 UTC"
    assert "The last check failed: RuntimeError: rule check failed" in h["action"]
    assert h["apply"] == {"kind": "none"} and h["meta"]["headsup_kind"] == "rule_check"
    # stale: two missed half-hours with no failure recorded (a wedged or dead job)
    [h2] = _rule_check_headsup(_state(last_ok=True, last_success="2026-10-23T10:30:00+00:00"), ARMED, now)
    assert "No check has completed" in h2["action"]
    # healthy, nothing armed, or no scheduler (tests, a worker-less deploy): nothing
    assert _rule_check_headsup(_state(last_ok=True, last_success="2026-10-23T11:40:00+00:00"), ARMED, now) == []
    assert _rule_check_headsup(_state(last_ok=False), [], now) == []
    assert _rule_check_headsup({"scheduler_running": False}, ARMED, now) == []


def test_today_carries_it(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as m
    from app.worker import scheduler as sch
    from tests.test_a_stop_out_is_not_bought_back import _pos
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [_pos("VTI", 60.0)]})
        r = c.post("/api/v1/rules", json={"ticker": "VTI", "rule_type": "stop_loss", "mode": "price", "level": 50.0})
        assert r.status_code == 200, r.text
        monkeypatch.setattr(sch, "job_state", lambda: _state(
            last_ok=False, last_success=None, last_error="RuntimeError: rule check failed"))
        t = c.get("/api/v1/recommendations").json()
        hs = [h for h in t.get("headsups") or [] if (h.get("card") or {}).get("meta", {}).get("headsup_kind") == "rule_check"]
        assert hs and hs[0]["title"].startswith("Your trading rules have not been checked"), \
            [h.get("title") for h in t.get("headsups") or []]
