"""Auth persistence (review C4): bcrypt credentials + DB-backed token revocation."""
from __future__ import annotations

import logging
import secrets

import bcrypt
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Role, decode_token, issue_pair
from app.core.config import get_settings
from app.models.tables import Credential, RevokedToken

logger = logging.getLogger("investwise.auth")

SUPERADMIN_EMAIL = "eran.ganot@gmail.com"

# Every password that has ever been a literal in this repository. The repository
# is PUBLIC, so each of these is published, not secret, and none may ever
# authenticate again -- not as a default, not as a leftover row, not if someone
# sets AUTH_PASSWORD back to one.
#
# Measured against production on 2026-08-30, before this change:
#
#     POST /api/v1/auth/token
#     {"email": "eran.ganot@gmail.com", "password": "changeme-dev"}
#     -> HTTP 200, SUPERADMIN access + refresh token
#
# ADDING TO THIS SET IS A ONE-WAY DOOR. It disables a password for everyone,
# for good. That is the intent: a credential that has been published cannot be
# un-published, and the only safe thing to do with it is refuse it.
PUBLISHED_PASSWORDS = frozenset({"changeme-dev"})


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


async def ensure_superadmin_credential(session: AsyncSession) -> None:
    """Make AUTH_PASSWORD authoritative, and never let a published one stand.

    THE BUG THIS REPLACES had two halves, and the second is why the first
    survived five months.

    1. `auth_password` defaulted to the literal "changeme-dev" in a PUBLIC
       repository. That is not a weak password, it is a published one.

    2. This function only ever INSERTED -- `if ... is None`. So the hash was
       written once, at first boot, from whatever the setting was then, and
       every later change to AUTH_PASSWORD was ignored. There is no
       password-change endpoint anywhere in this app. Setting the environment
       variable, the one remediation the docs tell you to perform
       (PRODUCTION.md section 8), therefore did nothing at all. The published
       password stayed live and no amount of configuration could retire it.

    What it does now, in order, on every call:

    * A configured password that is in PUBLISHED_PASSWORDS is treated as if it
      were not set. Refusing it here is what makes (1) unrepeatable.
    * AUTH_PASSWORD set, and the stored hash does not verify against it: the
      stored hash is REPLACED. The environment variable is now the source of
      truth, which is what fixes (2) and gives this app its only means of
      changing the SuperAdmin password.
    * AUTH_PASSWORD not set: no credential is created, and an existing row
      whose hash verifies a PUBLISHED password is scrambled to a random value.
      Password login becomes unavailable rather than open. Google sign-in is a
      separate path and is untouched.

    Fail-closed is deliberate. An unreachable login is an inconvenience Eran
    can fix with one Railway variable; a published one is a stranger holding
    SUPERADMIN over his money.

    ORDERING, which is the load-bearing part: /api/v1/auth/token awaits this
    BEFORE it calls verify_login (routes/auth.py). So the very first request to
    arrive after this deploys retires the published hash -- including the
    request of someone arriving to use it.
    """
    settings = get_settings()
    configured = (settings.auth_password or "").strip()

    if configured and configured in PUBLISHED_PASSWORDS:
        logger.error(
            "AUTH_PASSWORD is set to a value published in this repository. "
            "Refusing it. Password login stays disabled until AUTH_PASSWORD is "
            "set to something that has never been committed.")
        configured = ""

    res = await session.execute(select(Credential).where(Credential.email == SUPERADMIN_EMAIL))
    cred = res.scalar_one_or_none()

    if cred is None:
        if not configured:
            logger.warning(
                "AUTH_PASSWORD is not set, so no SuperAdmin credential was "
                "created and password login is unavailable. This is fail-closed, "
                "not a fault.")
            return
        session.add(Credential(email=SUPERADMIN_EMAIL,
                               password_hash=hash_password(configured),
                               role=Role.SUPERADMIN.value))
        await session.flush()
        return

    if configured:
        if not verify_password(configured, cred.password_hash):
            cred.password_hash = hash_password(configured)
            await session.flush()
            logger.warning("SuperAdmin password re-synced from AUTH_PASSWORD; "
                           "the previous password no longer works.")
        return

    # Nothing configured. Anything published that is still live must die here.
    for published in PUBLISHED_PASSWORDS:
        if verify_password(published, cred.password_hash):
            cred.password_hash = hash_password(secrets.token_urlsafe(48))
            await session.flush()
            logger.error(
                "The SuperAdmin password was a value published in this "
                "repository. It has been replaced with an unusable random "
                "value and password login is now disabled. Set AUTH_PASSWORD "
                "to restore it.")
            break


async def verify_login(session: AsyncSession, email: str, password: str) -> Role | None:
    res = await session.execute(select(Credential).where(Credential.email == email.lower()))
    cred = res.scalar_one_or_none()
    if cred and verify_password(password, cred.password_hash):
        return Role(cred.role)
    return None


async def is_jti_revoked(session: AsyncSession, jti: str) -> bool:
    res = await session.execute(select(RevokedToken).where(RevokedToken.jti == jti))
    return res.scalar_one_or_none() is not None


async def rotate_refresh(session: AsyncSession, refresh_token: str) -> dict:
    try:
        payload = decode_token(refresh_token)
    except Exception:  # noqa: BLE001
        raise HTTPException(401, "invalid refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(401, "not a refresh token")
    jti = payload.get("jti")
    if await is_jti_revoked(session, jti):
        raise HTTPException(401, "refresh token already used (rotated)")
    session.add(RevokedToken(jti=jti))           # single-use, survives restart + shared across instances
    await session.commit()
    return issue_pair(payload["sub"], Role(payload["role"]))
