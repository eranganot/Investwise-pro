"""The shared engine must validate connections before handing them out.

Symptom seen live: five API calls succeed, then the sixth and every call after
it returns NO HTTP STATUS AT ALL -- not a 4xx or 5xx, no response. The same
endpoint answers in 0.09s from a fresh process, and 15 identical calls in a row
never fail. That rules out rate limiting, a call counter and a slow endpoint.

Cause: Railway's Postgres closes idle connections. Without pool_pre_ping,
SQLAlchemy hands a dead connection to asyncpg, which waits forever for a reply
that never comes. push_service and pricing_service already built their own
engines with pool_pre_ping=True; the app's shared engine did not.

These assert the CONFIGURATION rather than the live engine, because the suite
runs on SQLite (NullPool) while production runs on Postgres (QueuePool) -- the
two expose different pool attributes.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.core.database import engine, engine_kwargs  # noqa: E402

PG = "postgresql+asyncpg://u:p@host:5432/db"
LITE = "sqlite+aiosqlite:///:memory:"


def test_pre_ping_is_on_for_every_backend():
    """Without it, a dead pooled connection hangs the request forever."""
    assert engine_kwargs(PG)["pool_pre_ping"] is True
    assert engine_kwargs(LITE)["pool_pre_ping"] is True
    assert engine.pool._pre_ping is True, "the live engine must have it too"


def test_postgres_recycles_before_the_far_end_drops_the_connection():
    kw = engine_kwargs(PG)
    assert 0 < kw["pool_recycle"] <= 900, "recycle should be minutes, not hours"


def test_pool_exhaustion_raises_rather_than_blocking():
    """A hang with no response is undiagnosable; a timeout is a clear error."""
    kw = engine_kwargs(PG)
    assert kw["pool_timeout"] > 0
    assert kw["pool_size"] >= 5 and kw["max_overflow"] >= 10


def test_sqlite_gets_no_sizing_options():
    """Regression: SQLite runs through NullPool, which REJECTS these outright.

    Passing them broke collection in all 30 test modules with
    'Invalid argument(s) pool_timeout, pool_size, max_overflow'.
    """
    kw = engine_kwargs(LITE)
    for banned in ("pool_size", "max_overflow", "pool_timeout", "pool_recycle"):
        assert banned not in kw, f"{banned} must not be passed to a SQLite engine"


def test_sql_echo_is_off_in_production():
    """echo=settings.debug logged every statement, and debug defaults to True."""
    assert engine_kwargs(PG, debug=True, environment="production")["echo"] is False
    assert engine_kwargs(PG, debug=True, environment="development")["echo"] is True
