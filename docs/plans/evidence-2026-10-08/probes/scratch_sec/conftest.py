import os

_DB = os.path.join(os.path.dirname(__file__), "sec.db").replace("\\", "/")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + _DB
os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["DEBUG"] = "false"
os.environ["WAR_ROOM_CACHE_TTL_SEC"] = "0"
os.environ["REQUIRE_AUTH"] = "true"
os.environ["ALLOWED_EMAILS"] = "eran.ganot@gmail.com"
os.environ["JWT_SECRET"] = "scratch-secret"
os.environ.pop("AGENT_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)
try:
    os.remove(_DB)
except OSError:
    pass

import pytest_asyncio  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def _isolate_db():
    import app.models  # noqa: F401
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings
    from app.models.base import Base
    get_settings.cache_clear()
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for t in reversed(Base.metadata.sorted_tables):
            await conn.execute(t.delete())
    await eng.dispose()
    try:
        from app.core.database import engine
        await engine.dispose()
    except Exception:
        pass
    yield


def tok(role="READ_ONLY", sub="eran.ganot@gmail.com"):
    from app.core.auth import Role, create_token
    return {"Authorization": "Bearer " + create_token(sub, Role(role), "access")}
