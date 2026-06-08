"""Auth persistence tests (review C4) - bcrypt + DB-backed rotation."""
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models.tables as tables  # noqa: F401
from app.core.auth import Role, issue_pair
from app.models.base import Base
from app.services.auth_service import (
    ensure_superadmin_credential, hash_password, rotate_refresh, verify_login, verify_password,
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


def test_bcrypt_hash_roundtrip():
    h = hash_password("s3cret")
    assert h != "s3cret"
    assert verify_password("s3cret", h) is True
    assert verify_password("wrong", h) is False


async def test_ensure_credential_and_login(session):
    await ensure_superadmin_credential(session)
    await ensure_superadmin_credential(session)  # idempotent
    await session.commit()
    role = await verify_login(session, "eran.ganot@gmail.com", "changeme-dev")
    assert role == Role.SUPERADMIN
    assert await verify_login(session, "eran.ganot@gmail.com", "nope") is None


async def test_db_refresh_rotation_single_use(session):
    pair = issue_pair("u", Role.SUPERADMIN)
    new = await rotate_refresh(session, pair["refresh_token"])
    assert "access_token" in new and "refresh_token" in new
    with pytest.raises(HTTPException):
        await rotate_refresh(session, pair["refresh_token"])  # revoked in DB
