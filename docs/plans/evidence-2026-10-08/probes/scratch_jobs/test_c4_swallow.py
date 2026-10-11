"""C4: do jobs record success in JOB_STATE when their inner work raises?

Each job is run through scheduler._guarded exactly as APScheduler would.
"""
import os
import tempfile

_DB = os.path.join(tempfile.mkdtemp(prefix="iwc4_"), "c4.db")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + _DB.replace("\\", "/")
os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["ENABLE_SCHEDULER"] = "false"

import asyncio  # noqa: E402

import pytest  # noqa: E402


def _create_tables():
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


@pytest.fixture(autouse=True)
def _fresh_state():
    _create_tables()
    from app.worker import scheduler
    scheduler.JOB_STATE.clear()
    yield


def _boom(*a, **k):
    raise RuntimeError("INJECTED")


async def _aboom(*a, **k):
    raise RuntimeError("INJECTED")


def _run(job_id, fn):
    from app.worker import scheduler
    scheduler._guarded(job_id, fn)()
    st = scheduler.JOB_STATE[job_id]
    print(f"\n[{job_id}] JOB_STATE = {st}")
    return st


# ---- the four suspects -------------------------------------------------------

def test_backtest_refresh(monkeypatch):
    from app.services import backtest_service as m
    monkeypatch.setattr(m, "refresh_all", _aboom)
    st = _run("backtest_refresh", m.run_backtest_refresh_blocking)
    assert st["last_ok"] is True and st["failures"] == 0   # swallowed


def test_strategy_signals(monkeypatch):
    from app.services import strategy_signal_service as m
    monkeypatch.setattr(m, "evaluate_all", _aboom)
    st = _run("strategy_signals", m.run_strategy_signals_blocking)
    assert st["last_ok"] is True and st["failures"] == 0


def test_push_evaluate(monkeypatch):
    from app.services import push_service as m
    monkeypatch.setattr(m, "_for_each_subscriber", _aboom)
    st = _run("push_evaluate", m.run_evaluations_blocking)
    assert st["last_ok"] is True and st["failures"] == 0


def test_push_digest(monkeypatch):
    from app.services import push_service as m
    monkeypatch.setattr(m, "_for_each_subscriber", _aboom)
    st = _run("push_digest", m.run_digests_blocking)
    assert st["last_ok"] is True and st["failures"] == 0


def test_push_evaluate_real_db_failure(monkeypatch, tmp_path):
    """No monkeypatch of app code: point the job at a DB with no tables."""
    from app.core import config
    from app.services import push_service as m
    empty = "sqlite+aiosqlite:///" + str(tmp_path / "empty.db").replace("\\", "/")
    real = config.get_settings()
    monkeypatch.setattr(m, "get_settings",
                        lambda: real.model_copy(update={"database_url": empty}))
    st = _run("push_evaluate", m.run_evaluations_blocking)
    assert st["last_ok"] is True


def test_backtest_refresh_real_db_failure(monkeypatch, tmp_path):
    from app.core import config
    from app.services import backtest_service as m
    empty = "sqlite+aiosqlite:///" + str(tmp_path / "empty.db").replace("\\", "/")
    real = config.get_settings()
    monkeypatch.setattr(m, "get_settings",
                        lambda: real.model_copy(update={"database_url": empty}))
    st = _run("backtest_refresh", m.run_backtest_refresh_blocking)
    assert st["last_ok"] is True


# ---- futures_warm: the closure inside start_scheduler -------------------------

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


def test_futures_warm(monkeypatch):
    import apscheduler.schedulers.background as bg

    from app.services import market_state, markets_service
    from app.worker import scheduler
    monkeypatch.setattr(bg, "BackgroundScheduler", _FakeSched)
    monkeypatch.setattr(market_state, "refresh_market_data", lambda: {})
    monkeypatch.setattr(markets_service, "futures_snapshot", _boom)
    scheduler.start_scheduler()
    job = scheduler._scheduler.jobs["futures_warm"]
    job()   # already _guarded
    st = scheduler.JOB_STATE["futures_warm"]
    print(f"\n[futures_warm] JOB_STATE = {st}")
    assert st["last_ok"] is True


def test_market_refresh(monkeypatch):
    from app.agents import research_agent
    from app.services import market_state
    monkeypatch.setattr(research_agent.ResearchAgent, "scan", _boom)
    st = _run("market_refresh", market_state.refresh_market_data)
    assert st["last_ok"] is True


# ---- controls: these should propagate ---------------------------------------

def test_price_refresh_propagates(monkeypatch):
    from app.services import pricing_service as m
    monkeypatch.setattr(m, "refresh_all_positions", _aboom)
    st = _run("price_refresh", m.run_price_refresh_blocking)
    assert st["last_ok"] is False and "INJECTED" in st["last_error"]


def test_nav_snapshot_propagates(monkeypatch, tmp_path):
    from app.core import config
    from app.services import nav_history as m
    monkeypatch.setattr(m, "is_trading_day", lambda *a, **k: (True, "stub"))
    empty = "sqlite+aiosqlite:///" + str(tmp_path / "empty.db").replace("\\", "/")
    real = config.get_settings()
    monkeypatch.setattr(config, "get_settings",
                        lambda: real.model_copy(update={"database_url": empty}))
    st = _run("nav_snapshot", m.run_nav_snapshot_blocking)
    assert st["last_ok"] is False


def test_solver_refresh_propagates(monkeypatch):
    from app.services import solver_store as m
    monkeypatch.setattr(m, "refresh_all", _aboom)
    st = _run("solver_refresh", m.run_solver_refresh_blocking)
    assert st["last_ok"] is False


def test_holding_review_propagates(monkeypatch):
    from app.services import holding_review as m
    monkeypatch.setattr(m, "_measure_all_async", _aboom)
    st = _run("holding_review", m.run_holding_review_blocking)
    assert st["last_ok"] is False


def test_standing_orders_propagates(monkeypatch):
    from app.services import standing_order_service as m
    monkeypatch.setattr(m, "run_due", _aboom)
    # need a due order for run_due to be reached; instead break the DB
    from app.core import config
    real = config.get_settings()
    monkeypatch.setattr(config, "get_settings",
                        lambda: real.model_copy(update={"database_url": "sqlite+aiosqlite:///Z:/nope/nope.db"}))
    st = _run("standing_orders", m.run_standing_orders_blocking)
    assert st["last_ok"] is False
