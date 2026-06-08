"""Data intake tests (Section 5)."""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models.tables as tables  # noqa: F401
from app.api.routes.intake import _row_to_position
from app.models.base import Base
from app.schemas.intake import IntakePosition
from app.schemas.state_machine import ActionType, Market
from app.services.feed_service import ensure_superadmin
from app.services.intake_service import (
    ensure_account, ensure_entity, list_positions, position_to_observation, upsert_positions,
)


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


def test_csv_row_parsing_with_blanks():
    p = _row_to_position({"ticker": "TEVA", "market": "NYSE", "depth": "3",
                          "spot_price": "100", "listing_price": "108.2",
                          "quantity": "500", "cost_basis": "90",
                          "expected_return_pct": "", "volatility_pct": "12", "action_type": ""})
    assert p.ticker == "TEVA"
    assert p.market == Market.NYSE
    assert p.expected_return_pct is None   # blank -> None
    assert p.volatility_pct == 12
    assert p.action_type == ActionType.BUY  # blank -> default


async def test_intake_persists_and_rebuilds_observations(session):
    user = await ensure_superadmin(session)
    entity = await ensure_entity(session, user, "Personal", "Personal")
    account = await ensure_account(session, entity, "Main")
    n = await upsert_positions(session, account, [
        IntakePosition(ticker="TEVA", market=Market.NYSE, depth=3, spot_price=100,
                       listing_price=108.2, quantity=500, cost_basis=90, volatility_pct=12),
    ])
    await session.commit()
    assert n == 1

    positions = await list_positions(session, user)
    assert len(positions) == 1
    obs = position_to_observation(positions[0])
    assert obs is not None
    assert obs.ticker == "TEVA" and obs.depth == 3
    assert obs.spot_price == 100 and obs.listing_price == 108.2


async def test_upsert_is_idempotent_by_ticker(session):
    user = await ensure_superadmin(session)
    entity = await ensure_entity(session, user, "Personal", "Personal")
    account = await ensure_account(session, entity, "Main")
    pos = [IntakePosition(ticker="X", market=Market.TASE, spot_price=100, listing_price=105)]
    await upsert_positions(session, account, pos)
    await upsert_positions(session, account, pos)  # second upload, same ticker
    await session.commit()
    assert len(await list_positions(session, user)) == 1


def test_observation_none_without_prices():
    class P:
        ticker = "Z"; market = "NYSE"; meta = {"depth": 1}
    assert position_to_observation(P()) is None
