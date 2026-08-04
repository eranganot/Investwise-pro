import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/iw_test_app.db")
os.environ.setdefault("AUTO_CREATE_TABLES", "true")
os.environ.setdefault("DEBUG", "false")

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
    yield
