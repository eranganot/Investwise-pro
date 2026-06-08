"""Learning Loop tests (Section 9)."""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models.tables as tables  # noqa: F401
from app.engines.learning_engine import compute_profile, impact_boost
from app.models.base import Base
from app.models.tables import DecisionFeed, DecisionItem, UserAction
from app.services.feed_service import ensure_superadmin


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


def test_impact_boost_thresholds():
    assert impact_boost({"total_actions": 0}, "Buy") == 1.0  # not enough data
    hi = {"total_actions": 5, "by_action_type": {"Buy": 0.8}}
    lo = {"total_actions": 5, "by_action_type": {"Buy": 0.2}}
    mid = {"total_actions": 5, "by_action_type": {"Buy": 0.5}}
    assert impact_boost(hi, "Buy") == 1.10
    assert impact_boost(lo, "Buy") == 0.90
    assert impact_boost(mid, "Buy") == 1.0


async def test_compute_profile_from_actions(session):
    user = await ensure_superadmin(session)
    feed = DecisionFeed(user_id=user.id, horizon="month", status="OPEN")
    session.add(feed)
    await session.flush()
    buy = DecisionItem(feed_id=feed.id, title="Buy A", action_type="Buy", veto_flag=False, time_sensitivity="Now")
    reb = DecisionItem(feed_id=feed.id, title="Rebalance B", action_type="Rebalance", veto_flag=False, time_sensitivity="Now")
    session.add_all([buy, reb])
    await session.flush()
    session.add_all([
        UserAction(user_id=user.id, decision_item_id=buy.id, action="accepted"),
        UserAction(user_id=user.id, decision_item_id=buy.id, action="accepted"),
        UserAction(user_id=user.id, decision_item_id=reb.id, action="ignored"),
    ])
    await session.commit()

    profile = await compute_profile(session, user.id)
    assert profile["total_actions"] == 3
    assert profile["acceptance_rate"] == pytest.approx(2 / 3, abs=0.01)
    assert profile["by_action_type"]["Buy"] == 1.0
    assert profile["by_action_type"]["Rebalance"] == 0.0
    assert "Buy" in profile["preferred_action_types"]
