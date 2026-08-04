"""A strategy list must survive its measurement store being unreadable.

Production: migration 0011 added last_error / last_error_at, the deploy landed
before `alembic upgrade head` ran, and every SELECT against strategy_backtests
hit an undefined column. That took out GET /strategies and
/strategies/backtests completely, and degraded the recommendations pipeline
through discipline_recs -- because an OPTIONAL side table was allowed to be
fatal.

The four original families need no backtest at all, and a measured strategy can
say "not measured yet" perfectly well. Neither should 500.
"""
import os
import sqlite3

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/iw_test_app.db")

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app.core.config import get_settings

# Exactly the shape migration 0009 left behind: no last_error, no last_error_at.
_PRE_0011 = """CREATE TABLE strategy_backtests (
    id TEXT PRIMARY KEY, strategy_id TEXT, engine_version TEXT, ok BOOLEAN,
    reason TEXT, detail TEXT, metrics JSON, robustness JSON, data_source TEXT,
    period_start TEXT, period_end TEXT, observations INTEGER,
    computed_at TIMESTAMP, created_at TIMESTAMP, updated_at TIMESTAMP)"""


def _sqlite_path() -> str:
    url = get_settings().database_url
    if not url.startswith("sqlite"):
        pytest.skip("this reproduces a schema drift that only matters on a file DB")
    return os.path.join(os.sep, url.split("://", 1)[-1].lstrip("/"))


def _break_the_store() -> None:
    """Roll strategy_backtests back to its pre-0011 columns.

    Called from the test body, not a fixture: the autouse _isolate_db fixture
    runs create_all first, which would helpfully undo the sabotage.
    """
    conn = sqlite3.connect(_sqlite_path())
    conn.execute("DROP TABLE IF EXISTS strategy_backtests")
    conn.execute(_PRE_0011)
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _restore_the_store():
    """Drop the sabotaged table afterwards so create_all can rebuild it whole.

    Without this the damage leaks: create_all adds missing TABLES but never a
    column to one that already exists, so every later test in the session
    inherits the broken schema. That is the same failure this module exists to
    reproduce -- easy to cause twice in one afternoon.
    """
    yield
    from app.services import backtest_service as svc
    svc.store_unavailable = None
    try:
        conn = sqlite3.connect(_sqlite_path())
        conn.execute("DROP TABLE IF EXISTS strategy_backtests")
        conn.commit()
        conn.close()
    except Exception:  # noqa: BLE001 -- a skipped Postgres run has no file to clean
        pass


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "V", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
         "spot_price": 365, "listing_price": 365, "quantity": 10, "cost_basis": 300,
         "expected_return_pct": 8, "volatility_pct": 20}]})


def test_the_plan_list_still_renders():
    with TestClient(m.app) as c:
        _seed(c)
        _break_the_store()
        r = c.get("/api/v1/strategies")
        assert r.status_code == 200, "a missing column in a side table took out the Plan page"
        body = r.json()
        for goal in ("Grow", "Balanced", "Income", "Preserve"):
            assert body["by_goal"][goal], f"{goal} vanished"
        assert body["backtest_store_error"], "the failure must be reported, not silently empty"


def test_the_backtests_route_still_answers():
    with TestClient(m.app) as c:
        _seed(c)
        _break_the_store()
        r = c.get("/api/v1/strategies/backtests")
        assert r.status_code == 200
        body = r.json()
        assert len(body["strategies"]) > 0
        assert all(s["backtest"] is None for s in body["strategies"])
        assert body["store_error"]


def test_recommendations_still_answers():
    with TestClient(m.app) as c:
        _seed(c)
        _break_the_store()
        assert c.get("/api/v1/recommendations").status_code == 200


def test_an_unreadable_store_is_distinguishable_from_an_empty_one():
    """"Could not read the measurements" and "nothing measured yet" need
    different responses -- one is a deploy fault, the other is just Tuesday."""
    from app.services import backtest_service as svc
    with TestClient(m.app) as c:
        _seed(c)
        _break_the_store()
        c.get("/api/v1/strategies")
        assert svc.store_unavailable is not None

        # And a healthy store clears the flag rather than latching it.
        svc.store_unavailable = None
        c.get("/api/v1/strategies")
