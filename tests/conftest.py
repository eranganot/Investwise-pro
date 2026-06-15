import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/iw_test_app.db")
os.environ.setdefault("AUTO_CREATE_TABLES", "true")
os.environ.setdefault("DEBUG", "false")

import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def _isolate_db():
    """Give every test a clean database: ensure the schema exists, then wipe all
    rows before the test runs. Eliminates cross-test state leakage (no more
    per-test cleanup hacks)."""
    import app.models  # noqa: F401  register all tables on Base.metadata
    from app.core.database import engine
    from app.models.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    yield
