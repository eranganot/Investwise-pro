"""Auth persistence tests (review C4) - bcrypt + DB-backed rotation."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models.tables as tables  # noqa: F401
from app.core.auth import Role, issue_pair
from app.core.config import get_settings
from app.models.base import Base
from app.models.tables import Credential
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


async def test_ensure_credential_and_login(session, monkeypatch):
    """The seeded password comes from AUTH_PASSWORD and nowhere else.

    This test USED TO ASSERT THE HOLE. It logged in with "changeme-dev" and
    passed, every run, for five months -- because that string was the default
    in config.py and the test hard-coded it too. A test written against the
    default cannot notice that the default is published.
    """
    monkeypatch.setenv("AUTH_PASSWORD", "a-value-never-committed-8f21")
    get_settings.cache_clear()
    try:
        await ensure_superadmin_credential(session)
        await ensure_superadmin_credential(session)  # idempotent
        await session.commit()
        role = await verify_login(session, "eran.ganot@gmail.com", "a-value-never-committed-8f21")
        assert role == Role.SUPERADMIN
        assert await verify_login(session, "eran.ganot@gmail.com", "nope") is None
    finally:
        get_settings.cache_clear()


async def test_a_published_password_cannot_log_in_even_if_it_is_configured(session, monkeypatch):
    """THE ONE THAT WOULD HAVE CAUGHT THE LIVE HOLE.

    "changeme-dev" was the default in a public repo and production accepted it
    on 2026-08-30. Setting it deliberately must now be refused too, otherwise
    the fix lasts exactly until someone puts the old value back to "keep the
    scripts working".
    """
    monkeypatch.setenv("AUTH_PASSWORD", "changeme-dev")
    get_settings.cache_clear()
    try:
        await ensure_superadmin_credential(session)
        await session.commit()
        assert await verify_login(session, "eran.ganot@gmail.com", "changeme-dev") is None
    finally:
        get_settings.cache_clear()


async def test_no_password_configured_means_no_credential_at_all(session, monkeypatch):
    """Fail closed. Blank AUTH_PASSWORD must not seed a login, of any kind."""
    monkeypatch.setenv("AUTH_PASSWORD", "")
    get_settings.cache_clear()
    try:
        await ensure_superadmin_credential(session)
        await session.commit()
        res = await session.execute(
            select(Credential).where(Credential.email == "eran.ganot@gmail.com"))
        assert res.scalar_one_or_none() is None, (
            "a blank AUTH_PASSWORD seeded a credential, which is how a default "
            "becomes a password again")
    finally:
        get_settings.cache_clear()


async def test_an_existing_published_hash_is_retired_on_the_next_call(session, monkeypatch):
    """Eran's production row, exactly: seeded long ago from "changeme-dev".

    The old code only ever INSERTED, so this row was untouchable -- no endpoint
    changed it and the environment variable was ignored. Reproduce it by hand
    and prove the next call kills it, with nothing configured to replace it.
    """
    session.add(Credential(email="eran.ganot@gmail.com",
                           password_hash=hash_password("changeme-dev"),
                           role=Role.SUPERADMIN.value))
    await session.commit()
    assert await verify_login(session, "eran.ganot@gmail.com", "changeme-dev") == Role.SUPERADMIN

    monkeypatch.setenv("AUTH_PASSWORD", "")
    get_settings.cache_clear()
    try:
        await ensure_superadmin_credential(session)
        await session.commit()
        assert await verify_login(session, "eran.ganot@gmail.com", "changeme-dev") is None, (
            "the published password still works after remediation")
    finally:
        get_settings.cache_clear()


async def test_auth_password_actually_changes_the_password(session, monkeypatch):
    """The capability this app did not have.

    There is no password-change endpoint. Before this change the hash was
    written once and AUTH_PASSWORD was read exactly never again, so rotating
    the SuperAdmin password was impossible without editing the database by
    hand. This asserts the environment variable is now authoritative.
    """
    session.add(Credential(email="eran.ganot@gmail.com",
                           password_hash=hash_password("the-old-one-9c3a"),
                           role=Role.SUPERADMIN.value))
    await session.commit()

    monkeypatch.setenv("AUTH_PASSWORD", "the-new-one-4d17")
    get_settings.cache_clear()
    try:
        await ensure_superadmin_credential(session)
        await session.commit()
        assert await verify_login(session, "eran.ganot@gmail.com", "the-new-one-4d17") == Role.SUPERADMIN
        assert await verify_login(session, "eran.ganot@gmail.com", "the-old-one-9c3a") is None
    finally:
        get_settings.cache_clear()


async def test_db_refresh_rotation_single_use(session):
    pair = issue_pair("u", Role.SUPERADMIN)
    new = await rotate_refresh(session, pair["refresh_token"])
    assert "access_token" in new and "refresh_token" in new
    with pytest.raises(HTTPException):
        await rotate_refresh(session, pair["refresh_token"])  # revoked in DB
