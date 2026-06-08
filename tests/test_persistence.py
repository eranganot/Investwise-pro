"""Persistence + output-contract tests (Section 4 schema, Section 7 contract)."""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models.tables as tables  # noqa: F401 (register mappers)
from app.models.base import Base
from app.models.tables import DecisionFeed, DecisionItem
from app.schemas.state_machine import (
    ActionType, DetectedSignal, DisplayedItem, Market, OptimizedSignal,
    RankedSignal, VettedSignal,
)
from app.services.feed_service import build_recommendation, ensure_superadmin


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


def test_build_recommendation_maps_output_contract():
    det = DetectedSignal(
        ticker="TEVA", market=Market.NYSE, action_type=ActionType.BUY,
        trigger="Depth 3 backbone divergence", depth=3, divergence_pct=8.2,
        expected_return_pct=10.0, gross_gain_ils=100_000,
    )
    vet = VettedSignal(source=det, probability_of_ruin=0.03, max_drawdown=0.09,
                       risk_critique="Within risk limits.")
    opt = OptimizedSignal(source=vet, net_gain_delta=75_000, tax_saved=10_000)
    ranked = RankedSignal(source=opt, impact_score=32.0, confidence=75.0,
                          complexity=2, urgency=48, r_score=50, t_score=75, risk_score=97)
    item = DisplayedItem(source=ranked, path="Growth", title="Buy TEVA (NYSE)")

    rec = build_recommendation(item)
    assert rec.title == "Buy TEVA (NYSE)"
    assert rec.action_type == "Buy"
    assert rec.expected_impact.tax_impact == 10_000
    assert rec.time_sensitivity == "This Week"  # urgency 48 -> This Week
    assert rec.risk_critique == "Within risk limits."
