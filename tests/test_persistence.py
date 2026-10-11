"""Persistence + output-contract tests (Section 4 schema, Section 7 contract)."""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models.tables as tables  # noqa: F401 (register mappers)
from app.models.base import Base
from app.models.tables import DecisionFeed, DecisionItem
from app.services.feed_service import ensure_superadmin


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


async def test_ensure_superadmin_is_idempotent(session):
    u1 = await ensure_superadmin(session)
    await session.commit()
    u2 = await ensure_superadmin(session)
    assert u1.id == u2.id
    assert u1.email == "eran.ganot@gmail.com"


async def test_persist_feed_with_items(session):
    user = await ensure_superadmin(session)
    feed = DecisionFeed(user_id=user.id, horizon="month", status="OPEN")
    session.add(feed)
    await session.flush()
    session.add(DecisionItem(
        feed_id=feed.id, title="Buy X", action_type="Buy",
        impact_score=30.0, confidence=70.0, veto_flag=False,
        time_sensitivity="Now", payload={"k": "v"},
    ))
    await session.commit()
    rows = (await session.execute(select(DecisionItem))).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload == {"k": "v"}
