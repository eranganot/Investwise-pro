"""Auth + multi-entity tests (Phase 9)."""
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models.tables as tables  # noqa: F401
from app.core import security
from app.core.config import Settings
from app.models.base import Base
from app.schemas.intake import IntakePosition
from app.schemas.state_machine import Market
from app.services.feed_service import ensure_superadmin
from app.services.intake_service import (
    ensure_account, ensure_entity, get_entities, list_positions, upsert_positions,
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


async def test_auth_disabled_passes(monkeypatch):
    monkeypatch.setattr(security, "get_settings", lambda: Settings(api_key=""))
    assert await security.require_api_key(None) is None


async def test_auth_enabled_enforced(monkeypatch):
    monkeypatch.setattr(security, "get_settings", lambda: Settings(api_key="k"))
    with pytest.raises(HTTPException):
        await security.require_api_key(None)
    with pytest.raises(HTTPException):
        await security.require_api_key("wrong")
    assert await security.require_api_key("k") is None


async def test_entities_listed_and_portfolio_filtered(session):
    user = await ensure_superadmin(session)
    e1 = await ensure_entity(session, user, "Personal", "Personal")
    a1 = await ensure_account(session, e1, "Main")
    await upsert_positions(session, a1, [IntakePosition(ticker="TEVA", market=Market.NYSE,
                           depth=3, spot_price=100, listing_price=108)])
    e2 = await ensure_entity(session, user, "Corp", "Corp")
    a2 = await ensure_account(session, e2, "Main")
    await upsert_positions(session, a2, [IntakePosition(ticker="GOLD", market=Market.SPOT,
                           spot_price=100, listing_price=104)])
    await session.commit()

    ents = await get_entities(session, user)
    assert {e["name"] for e in ents} == {"Personal", "Corp"}
    assert all(e["positions"] == 1 for e in ents)
    assert [p.ticker for p in await list_positions(session, user, "Corp")] == ["GOLD"]
    assert len(await list_positions(session, user)) == 2
