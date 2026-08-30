import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/iw_test_app.db")
os.environ.setdefault("AUTO_CREATE_TABLES", "true")
os.environ.setdefault("DEBUG", "false")

import pytest
import pytest_asyncio


def _drop_stale_sqlite_file() -> None:
    """Delete the sqlite test database before the session starts.

    ``create_all`` creates missing TABLES but never adds a column to a table
    that already exists, so the moment a model gains a field every test that
    touches it dies with "no such column" -- on a schema the code is right about
    and the leftover file is wrong about. It cost a full red suite twice: once
    in the sandbox, then again on the dev machine, because deleting the file
    locally fixed the symptom without fixing the cause.

    The file is a throwaway by design, so the honest fix is to stop carrying it
    between runs. Postgres runs (CI's test-postgres job) are untouched: they set
    DATABASE_URL explicitly and get their schema from migrations.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("sqlite"):
        return
    path = url.split("://", 1)[-1].lstrip("/")
    if not path or path == ":memory:":
        return
    for candidate in (os.path.join(os.sep, path), path):
        try:
            if os.path.isfile(candidate):
                os.remove(candidate)
        except OSError:
            # A locked file is not worth failing collection over; the per-test
            # fixture still truncates, so only a schema change actually breaks.
            pass


_drop_stale_sqlite_file()


@pytest_asyncio.fixture(autouse=True)
async def _isolate_db():
    """Clean DB before each test. Uses a throwaway NullPool engine created in the
    current event loop so it never reuses the app engine's pooled connection
    across loops (which asyncpg rejects with 'attached to a different loop')."""
    import app.models  # noqa: F401  register all tables on Base.metadata
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings
    from app.models.base import Base

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for table in reversed(Base.metadata.sorted_tables):
                await conn.execute(table.delete())
    finally:
        await eng.dispose()

    await _drop_app_pool()
    yield
    await _drop_app_pool()


async def _drop_app_pool() -> None:
    """Empty the APP engine's connection pool around every test.

    The cross-loop trap has now cost three red `test-postgres` runs in three
    different files, and the `own_session` fixture below only covers the shape I
    knew about: a test opening `AsyncSessionLocal` beside a client. It does not
    cover this one -- a `TestClient` used WITHOUT `with` runs every request in
    its own anyio portal loop, so request 2 borrows the pooled connection
    request 1 left behind and asyncpg rejects it with "attached to a different
    loop".

    Note who pays. Not the test that did it: whichever LATER test is first to
    touch the app engine. So it reads as an unrelated random failure, in a file
    nobody edited, and only ever on Postgres. That is why writing the rule down
    keeps not working.

    A pool with nothing in it cannot hand a connection across a loop boundary,
    which makes the whole family structurally impossible rather than documented.
    Costs one connect per test; this suite is bound by the provider stubs, not
    by that.
    """
    try:
        from app.core.database import engine
    except Exception:  # noqa: BLE001 -- no app engine configured; nothing to drop
        return
    try:
        await engine.dispose()
    except Exception:  # noqa: BLE001 -- disposal must never fail a test
        pass


@pytest.fixture
def own_session():
    """A session on a throwaway NullPool engine created in the TEST's own loop.

    Reaching for `AsyncSessionLocal` beside a `TestClient` passes on SQLite and
    fails on Postgres: the client has already driven the app's shared pooled
    engine in its own loop, so asyncpg rejects the connection with "attached to
    a different loop". This has now caused a red `test-postgres` twice, in two
    different test files, after the rule was written down in CLAUDE.md -- which
    is the evidence that a documented rule is not enough and it has to be the
    path of least resistance instead.

    Usage:

        async with own_session() as s:
            ...
    """
    from contextlib import asynccontextmanager

    from sqlalchemy.ext.asyncio import (
        AsyncSession, async_sessionmaker, create_async_engine,
    )
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings

    @asynccontextmanager
    async def _factory():
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
            async with maker() as session:
                yield session
        finally:
            await eng.dispose()

    return _factory


# --------------------------------------------------------------------------- #
# A SKIP IS NEVER A PASS -- enforced, not written down
# --------------------------------------------------------------------------- #
# The smoke scripts have said this for months and the suite did not. On
# 2026-08-30 the SQLite run reported "4 skipped" and the Postgres run "12", and
# nobody had read either number in a long time. What they were:
#
#   * 8 (Postgres only) -- test_backtest_store_unavailable.py reached for
#     `sqlite3.connect()` and skipped on anything else, so four tests protecting
#     a real PRODUCTION incident (migration 0011 shipping behind its deploy and
#     taking out /strategies) never ran on the backend production uses.
#   * 4 (both) -- a sleeve test parametrised over five books, four of which have
#     no sleeves. Correct behaviour, reported as four skips every run.
#
# Neither was a decision anyone made; both were invisible because "N skipped"
# scrolls past. A count that is allowed to drift is not a signal, so this makes
# it fail instead: any skip with a reason not registered below fails the run.
#
# TO ADD ONE: put a distinctive fragment of the reason in `_ALLOWED_SKIPS` with
# a comment saying why the test cannot run and what would let it. An entry here
# is a decision, which is the whole difference from the number nobody read.
_ALLOWED_SKIPS: tuple[str, ...] = ()

_OBSERVED_SKIPS: list[tuple[str, str]] = []


def pytest_runtest_logreport(report):
    if not report.skipped:
        return
    reason = ""
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        reason = str(longrepr[2])
    elif longrepr is not None:
        reason = str(longrepr)
    _OBSERVED_SKIPS.append((report.nodeid, reason))


def pytest_sessionfinish(session, exitstatus):
    unexpected = [(nodeid, reason) for nodeid, reason in _OBSERVED_SKIPS
                  if not any(allowed and allowed in reason for allowed in _ALLOWED_SKIPS)]
    if not unexpected:
        return
    print("\n" + "=" * 70)
    print(f"UNREGISTERED SKIP{'S' if len(unexpected) > 1 else ''}: "
          f"{len(unexpected)} test(s) did not run, and nothing decided that.")
    for nodeid, reason in unexpected:
        print(f"  {nodeid}\n      {reason}")
    print("")
    print("  A skip is never a pass. Either make the test run, or register the")
    print("  reason in tests/conftest.py::_ALLOWED_SKIPS with why it cannot.")
    print("=" * 70)
    session.exitstatus = 1
