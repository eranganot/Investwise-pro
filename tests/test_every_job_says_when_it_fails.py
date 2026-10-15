"""PR 107: a scheduled job that fails says so (D29), for EVERY job.

Proven 2026-10-08 and re-proven 2026-10-11 (probe docs/plans/evidence-2026-10-08/
probes/scratch_jobs/test_c4_swallow.py): with a raise injected, or pointed at a
database with no tables, six jobs recorded `last_ok: True` -- backtest_refresh,
strategy_signals, push_evaluate, push_digest, futures_warm, market_refresh. Each
caught everything and returned its healthy-empty shape ({"sent": 0} is also
what a quiet hour looks like), so /push/status showed green while nothing ran.

This test does not trust a list of jobs. It runs `start_scheduler()` against a
fake scheduler, captures every `add_job`, and requires each captured job to
(a) record `last_ok: True` on a healthy run and (b) record `last_ok: False`
when its inner work raises. A new job with no entry in FAIL_WITH fails here,
which is the point: a job nothing calls is a job nobody has tested.
"""
import asyncio

import pytest

INJECTED = "INJECTED-PR107"


def _boom(*a, **k):
    raise RuntimeError(INJECTED)


async def _aboom(*a, **k):
    raise RuntimeError(INJECTED)


def _fail_prices(mp):
    from app.services import pricing_service
    mp.setattr(pricing_service, "refresh_all_positions", _aboom)


def _fail_futures(mp):
    from app.services import markets_service
    mp.setattr(markets_service, "_http_text", _boom)


def _fail_push(mp):
    from app.services import push_service
    mp.setattr(push_service, "_for_each_subscriber", _aboom)


def _fail_nav(mp):
    """One user's snapshot raises: the job used to count it and report success."""
    from app.services import nav_history
    _seed_user()
    mp.setattr(nav_history, "is_trading_day", lambda *a, **k: (True, "stub"))
    mp.setattr(nav_history, "record_for", _aboom)


def _fail_backtests(mp):
    from app.services import backtest_service
    mp.setattr(backtest_service, "refresh_all", _aboom)


def _fail_solver(mp):
    from app.services import solver_store
    mp.setattr(solver_store, "refresh_all", _aboom)


def _fail_correlation(mp):
    from app.services import sleeve_diversity
    mp.setattr(sleeve_diversity, "measure_blocking", _boom)


def _fail_review(mp):
    from app.services import holding_review
    mp.setattr(holding_review, "_measure_all_async", _aboom)


def _fail_signals(mp):
    from app.services import strategy_signal_service
    mp.setattr(strategy_signal_service, "evaluate_all", _aboom)


def _fail_orders(mp):
    from app.core import config
    real = config.get_settings()
    mp.setattr(config, "get_settings", lambda: real.model_copy(
        update={"database_url": "sqlite+aiosqlite:///Z:/no/such/dir/nope.db"}))


# job id -> how to make its inner work raise. EVERY job must be here.
FAIL_WITH = {
    "price_refresh": _fail_prices,
    "futures_warm": _fail_futures,
    "push_evaluate": _fail_push,
    "push_digest": _fail_push,
    "nav_snapshot": _fail_nav,
    "backtest_refresh": _fail_backtests,
    "solver_refresh": _fail_solver,
    "strategy_correlation": _fail_correlation,
    "holding_review": _fail_review,
    "strategy_signals": _fail_signals,
    "standing_orders": _fail_orders,
}


class _FakeSched:
    def __init__(self, *a, **k):
        self.jobs = {}
        self.running = False

    def add_job(self, fn, *a, id=None, **k):
        self.jobs[id] = fn

    def start(self):
        pass

    def get_jobs(self):
        return []

    def shutdown(self, wait=False):
        pass


@pytest.fixture
def captured(monkeypatch):
    """Every job `start_scheduler` registers, already wrapped by `_guarded`."""
    import apscheduler.schedulers.background as bg

    from app.worker import scheduler
    monkeypatch.setattr(bg, "BackgroundScheduler", _FakeSched)
    scheduler.JOB_STATE.clear()
    scheduler.start_scheduler()
    jobs = dict(scheduler._scheduler.jobs)
    scheduler._scheduler = None
    return jobs


def _seed_user(email="jobs@test.local"):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings
    from app.models.tables import User

    async def go():
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(eng, class_=AsyncSession)() as s:
                s.add(User(email=email, name="jobs"))
                await s.commit()
        finally:
            await eng.dispose()
    _tables()
    asyncio.run(go())


def _tables():
    import app.models  # noqa: F401
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings
    from app.models.base import Base

    async def go():
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        await eng.dispose()
    asyncio.run(go())


def test_every_registered_job_has_a_failure_test(captured):
    assert set(captured) == set(FAIL_WITH), (
        "a scheduled job was added or removed; give it a FAIL_WITH entry so this test can prove "
        f"it reports failure. registered={sorted(captured)} tested={sorted(FAIL_WITH)}")


@pytest.mark.parametrize("job_id", sorted(FAIL_WITH))
def test_a_job_that_fails_records_the_failure(job_id, captured, monkeypatch):
    from app.worker import scheduler
    _tables()
    FAIL_WITH[job_id](monkeypatch)
    captured[job_id]()                      # `_guarded`, exactly as APScheduler runs it
    st = scheduler.JOB_STATE[job_id]
    assert st["last_ok"] is False and st["failures"] == 1, (
        f"{job_id} swallowed its failure and reported success: {st}")


def test_one_failing_push_subscriber_fails_the_run_but_not_the_others(captured, monkeypatch):
    """The fan-out used to abort on the first bad subscriber AND report success."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings
    from app.models.tables import PushSubscription
    from app.services import push_service
    from app.worker import scheduler

    _tables()

    async def seed():
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(eng, class_=AsyncSession)() as s:
                for i, subj in enumerate(("bad@test.local", "good@test.local")):
                    s.add(PushSubscription(subject=subj, endpoint=f"https://push.test/{i}",
                                           p256dh="k", auth="a"))
                await s.commit()
        finally:
            await eng.dispose()
    asyncio.run(seed())

    seen = []

    async def evaluate(session, user):
        seen.append(user.email)
        if user.email == "bad@test.local":
            raise RuntimeError(INJECTED)
        return {"sent": 1}

    monkeypatch.setattr(push_service, "evaluate_and_notify", evaluate)
    captured["push_evaluate"]()
    st = scheduler.JOB_STATE["push_evaluate"]
    assert sorted(seen) == ["bad@test.local", "good@test.local"], seen
    assert st["last_ok"] is False and "1 of 2" in st["last_error"], st


def test_a_futures_outage_keeps_the_last_snapshot(monkeypatch):
    """L10: an outage used to overwrite a good cache with [] -> a silent "neutral"."""
    import json

    from app.services import markets_service as ms
    good = {"chart": {"result": [{"meta": {"regularMarketPrice": 30.0,
                                           "chartPreviousClose": 20.0, "currency": "USD"}}]}}
    monkeypatch.setattr(ms, "_http_text", lambda url, *a, **k: json.dumps(good))
    first = ms.futures_snapshot(force=True)
    assert len(first["futures"]) == len(ms.FUTURES)
    monkeypatch.setattr(ms, "_http_text", _boom)
    with pytest.raises(ms.FuturesUnavailable):
        ms.futures_snapshot(force=True, raise_on_outage=True)
    kept = ms.futures_snapshot()
    assert kept["futures"] == first["futures"] and kept.get("stale") is True
    # PR 108: the regime read from a stale snapshot is UNKNOWN ({}), never a
    # silent "neutral" (the L10 bug) and never yesterday's reading passed off
    # as today's.
    assert ms.cached_regime() == {}


def test_the_http_cap_is_a_cap(monkeypatch):
    """L11: leaving a `with ThreadPoolExecutor` joined the hung worker (10 s vs 6 s)."""
    import time
    import urllib.request

    from app.providers import live

    def hung(req, timeout=None):
        time.sleep(4)
        raise OSError("late")
    monkeypatch.setattr(urllib.request, "urlopen", hung)
    t0 = time.perf_counter()
    with pytest.raises(Exception):
        live._http_text("http://x", timeout=-4.0)        # cap = timeout + 5 = 1 s
    assert time.perf_counter() - t0 < 2.5


@pytest.mark.parametrize("job_id", sorted(FAIL_WITH))
def test_a_healthy_job_records_success(job_id, captured, monkeypatch):
    """The other half: removing the swallow must not turn a quiet run red."""
    import json

    from app.services import markets_service as ms
    from app.worker import scheduler
    _tables()
    good = {"chart": {"result": [{"meta": {"regularMarketPrice": 30.0,
                                           "chartPreviousClose": 29.0, "currency": "USD"}}]}}
    monkeypatch.setattr(ms, "_http_text", lambda url, *a, **k: json.dumps(good))
    captured[job_id]()
    st = scheduler.JOB_STATE[job_id]
    assert st["last_ok"] is True, f"{job_id} failed on a healthy run: {st.get('last_error')}"


def test_boot_does_not_wait_for_the_futures(monkeypatch):
    """L11: startup called the futures warm directly, so boot took as long as the
    slowest of eight Yahoo fetches (a 12 s stub -> a 12.1 s boot). It is primed
    through the scheduler now, like price_refresh."""
    import time

    import apscheduler.schedulers.background as bg

    from app.agents import research_agent
    from app.services import markets_service as ms
    from app.worker import scheduler

    def slow(url, *a, **k):
        time.sleep(5)
        raise TimeoutError("stub: slow provider")
    monkeypatch.setattr(ms, "_http_text", slow)
    monkeypatch.setattr(research_agent.ResearchAgent, "scan", lambda self: time.sleep(5))
    monkeypatch.setattr(bg, "BackgroundScheduler", _FakeSched)
    t0 = time.perf_counter()
    scheduler.start_scheduler()
    scheduler._scheduler = None
    assert time.perf_counter() - t0 < 2.0
