"""Planning/goals tests."""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models.tables as tables  # noqa: F401
from app.models.base import Base
from app.services.feed_service import ensure_superadmin
from app.services.plan_service import effective_caps, get_plan, upsert_plan


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


def test_effective_caps_flex_with_risk_tolerance():
    assert effective_caps(None)["concentration_cap"] == 0.25
    class P:  # lightweight stand-in
        risk_tolerance = "High"
    assert effective_caps(P())["concentration_cap"] == 0.40
    class L:
        risk_tolerance = "Low"
    assert effective_caps(L())["concentration_cap"] == 0.15


async def test_upsert_and_get_plan(session):
    user = await ensure_superadmin(session)
    p = await upsert_plan(session, user, objective="Grow", risk_tolerance="High",
                          horizon_years=15, target_amount=2_000_000, target_date="2035")
    await session.commit()
    got = await get_plan(session, user)
    assert got.objective == "Grow" and got.risk_tolerance == "High"
    assert float(got.target_amount) == 2_000_000
    updated = await upsert_plan(session, user, risk_tolerance="Low")
    assert updated.risk_tolerance == "Low" and updated.objective == "Grow"  # partial update keeps rest
