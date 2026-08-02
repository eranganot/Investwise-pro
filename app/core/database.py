"""Async SQLAlchemy engine + session factory."""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()

# Engine is created lazily; it does NOT connect until first use.
#
# pool_pre_ping is not optional here. Railway's Postgres closes idle
# connections, and without a liveness check SQLAlchemy hands a dead one to
# asyncpg, which then waits for a reply that never arrives -- the request hangs
# with no HTTP status at all. Symptom seen live: five API calls succeed, the
# sixth and everything after it return nothing, while the same endpoint answers
# in 0.09s from a fresh process. Note that push_service and pricing_service
# already build their own engines with pool_pre_ping=True; the app's shared
# engine was the one place still missing it.
#
# echo=debug was also wrong for production: `debug` defaults to True, so unless
# DEBUG is explicitly set every statement was being logged.
def engine_kwargs(url: str, *, debug: bool = False, environment: str = "development") -> dict:
    """Engine options for a database URL.

    Sizing options are Postgres-only: SQLite (used by the whole test suite) is
    driven through NullPool, which rejects pool_size / max_overflow /
    pool_timeout outright. pool_pre_ping is safe on both.
    """
    kw: dict = {
        "echo": bool(debug) and environment != "production",
        "future": True,
        "pool_pre_ping": True,   # validate before handing out; drops dead sockets
    }
    if not url.startswith("sqlite"):
        kw.update({
            "pool_recycle": 300,   # retire connections before the far end does
            "pool_timeout": 30,    # exhaustion raises instead of blocking forever
            "pool_size": 10,
            "max_overflow": 20,
        })
    return kw


engine = create_async_engine(
    settings.database_url,
    **engine_kwargs(settings.database_url, debug=settings.debug,
                    environment=settings.environment),
)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async DB session."""
    async with AsyncSessionLocal() as session:
        yield session
