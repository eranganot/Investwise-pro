"""PR 144 -- a build or a background job that is broken says so, on Today and on the phone.

CI does not gate a deploy (Railway deploys every push) and GitHub Actions is off
until 2026-11-01. `run_nav_snapshot_blocking` once failed 7 nights out of 7 on an
import inside the function, seen by nobody: job state was one line on
`/push/status`. PR 143 put a failed RULE CHECK on Today; nothing else was watched,
and no heads-up was ever pushed (the push loop reads `recommendations` only).
"""
import os
import types

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# 1. the boot self-check
# --------------------------------------------------------------------------- #
def _module(src: str, name="fake_job_mod"):
    m = types.ModuleType(name)
    m.__file__ = f"/{name}.py"
    exec(compile(src, m.__file__, "exec"), m.__dict__)
    m._src = src
    return m


def test_a_lazy_import_that_cannot_resolve_is_found(monkeypatch):
    """The nav_snapshot failure, reproduced: the name does not exist and nothing
    sees it until the job runs."""
    import inspect

    from app.worker import self_check as sc
    m = _module("def run_job():\n    from app.core.database import async_session\n    return async_session\n"
                "def fine():\n    from app.core.database import AsyncSessionLocal\n    return AsyncSessionLocal\n")
    monkeypatch.setattr(inspect, "getsource", lambda mod: mod._src if hasattr(mod, "_src") else "")
    errs = sc.lazy_import_errors(m)
    assert errs == ["fake_job_mod.run_job: app.core.database has no 'async_session'"]


def test_every_real_job_resolves_what_it_imports_when_it_runs(monkeypatch):
    """The guard: a job module with a broken in-function import fails HERE, in the
    suite, before it can fail at 22:10 in production. The job list is the real
    one: `start_scheduler` runs against a scheduler that only records."""
    import apscheduler.schedulers.background as bg

    from app.worker import scheduler as sch
    from app.worker.self_check import check_jobs

    class _Rec:
        def __init__(self, *a, **k):
            self.jobs = []
            self.running = False

        def add_job(self, fn, *a, **k):
            self.jobs.append(k.get("id"))

        def start(self):
            pass

        def get_jobs(self):
            return []
    monkeypatch.setattr(bg, "BackgroundScheduler", _Rec)
    monkeypatch.setattr(sch, "ENTRY_POINTS", {})
    monkeypatch.setattr(sch, "load_persisted_state", lambda: 0)
    monkeypatch.setattr(sch, "run_boot_check", lambda: {})
    monkeypatch.setattr(sch, "_scheduler", None)
    sch.start_scheduler()
    assert len(sch.ENTRY_POINTS) >= 13, sorted(sch.ENTRY_POINTS)
    res = check_jobs(dict(sch.ENTRY_POINTS))
    assert res["ok"], res["errors"]


def test_the_boot_check_is_recorded_as_a_job(monkeypatch):
    from app.worker import scheduler as sch
    monkeypatch.setattr(sch, "JOB_STATE", {})
    monkeypatch.setattr(sch, "_persist", lambda jid, st: None)
    monkeypatch.setattr(sch, "ENTRY_POINTS", {"x": len})
    sch.run_boot_check()
    st = sch.JOB_STATE["boot_check"]
    assert st["last_ok"] is True
    assert "registered_at" in sch.JOB_STATE["x"]            # a job with no history is registered

    from app.worker import self_check
    monkeypatch.setattr(self_check, "check_jobs", lambda eps: {"ok": False, "errors": ["m.f: a has no 'b'"]})
    sch.run_boot_check()
    assert sch.JOB_STATE["boot_check"]["last_ok"] is False
    assert "a has no 'b'" in sch.JOB_STATE["boot_check"]["last_error"]


# --------------------------------------------------------------------------- #
# 2. the heads-up
# --------------------------------------------------------------------------- #
def _state(**hist):
    return {"scheduler_running": True, "history": hist}


def test_a_failing_job_is_a_heads_up():
    from app.services.recommendations import _job_health_headsup
    [h] = _job_health_headsup(_state(
        nav_snapshot={"last_ok": False, "last_error": "ImportError: cannot import name 'async_session'"},
        futures_warm={"last_ok": True, "last_success": "2026-10-24T08:55:00+00:00"}), NOW)
    assert h["title"] == "1 background job is not working"
    assert "nav_snapshot failing: ImportError" in h["action"]
    assert h["apply"] == {"kind": "none"} and h["meta"]["headsup_kind"] == "job_health"


def test_a_daily_job_that_stopped_or_never_ran_is_a_heads_up():
    from app.services.recommendations import _job_health_headsup
    [h] = _job_health_headsup(_state(
        strategy_signals={"last_ok": True, "last_success": "2026-10-22T06:15:00+00:00"},
        push_digest={"runs": 0, "registered_at": "2026-10-23T05:00:00+00:00"}), NOW)
    assert h["meta"]["jobs"] == ["push_digest", "strategy_signals"]
    assert "push_digest never succeeded since 23 Oct 05:00 UTC" in h["action"]
    assert "strategy_signals last succeeded since 22 Oct 06:15 UTC" in h["action"]


def test_a_broken_build_says_so_first():
    from app.services.recommendations import _job_health_headsup
    [h] = _job_health_headsup(_state(boot_check={"last_ok": False, "last_error": "x.f: y has no 'z'"}), NOW)
    assert h["title"] == "This build cannot run some of its own jobs"


def test_healthy_jobs_and_the_price_job_raise_nothing_here():
    from app.services.recommendations import _job_health_headsup
    assert _job_health_headsup(_state(
        boot_check={"last_ok": True},
        price_refresh={"last_ok": False},                      # PR 143's heads-up says this one
        nav_snapshot={"last_ok": True, "last_success": "2026-10-23T22:10:00+00:00"},
        push_digest={"runs": 0, "registered_at": "2026-10-24T08:00:00+00:00"}), NOW) == []
    assert _job_health_headsup({"scheduler_running": False}, NOW) == []


# --------------------------------------------------------------------------- #
# 3. the push
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_app_health_heads_ups_are_pushed_once(monkeypatch):
    from app.services import push_service as ps

    sent, marks = [], set()

    async def fake_build(session, user):
        return {"recommendations": [], "headsups": [
            {"id": "h1", "title": "x", "card": {"id": "rec_jh1", "title": "1 background job is not working",
                                                "action": "nav_snapshot failing", "meta": {"headsup_kind": "job_health"}}},
            {"id": "h2", "title": "y", "card": {"id": "rec_other", "title": "SOXL fell 11% in a day",
                                                "meta": {"headsup_kind": "big_move"}}}]}

    async def send(session, subject, title, body, url=None, tag=None):
        sent.append((title, tag))
        return 1

    async def seen_within(session, subject, sig, hours):
        return sig in marks

    async def mark(session, subject, sig):
        marks.add(sig)

    import app.services.recommendations as R
    monkeypatch.setattr(R, "build_recommendations", fake_build)
    monkeypatch.setattr(ps, "send_to_subject", send)
    monkeypatch.setattr(ps, "_seen_within", seen_within)
    monkeypatch.setattr(ps, "_mark", mark)
    import app.services.portfolio_analytics as pa
    monkeypatch.setattr(pa, "load_positions", lambda *a, **k: _none())

    class _S:                                    # a subscriber exists; nothing else is read
        async def scalar(self, *a, **k):
            return object()

        async def commit(self):
            pass

        async def execute(self, *a, **k):
            raise RuntimeError("not used")
    for _ in range(2):
        await ps.evaluate_and_notify(_S(), types.SimpleNamespace(email="e@x"))
    health = [t for t in sent if "background job" in t[0]]
    assert len(health) == 1 and health[0][1] == "trigger:app_health:rec_jh1", sent
    assert not [t for t in sent if "SOXL" in t[0]]


async def _none():
    return []
