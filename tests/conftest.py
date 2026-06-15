import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/iw_test_app.db")
os.environ.setdefault("AUTO_CREATE_TABLES", "true")
os.environ.setdefault("DEBUG", "false")

import pytest_asyncio


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
