"""Auth persistence (review C4): bcrypt credentials + DB-backed token revocation."""
from __future__ import annotations

import bcrypt
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Role, decode_token, issue_pair
from app.core.config import get_settings
from app.models.tables import Credential, RevokedToken

SUPERADMIN_EMAIL = "eran.ganot@gmail.com"


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


async def ensure_superadmin_credential(session: AsyncSession) -> None:
    res = await session.execute(select(Credential).where(Credential.email == SUPERADMIN_EMAIL))
    if res.scalar_one_or_none() is None:
        session.add(Credential(email=SUPERADMIN_EMAIL,
                               password_hash=hash_password(get_settings().auth_password),
                               role=Role.SUPERADMIN.value))
        await session.flush()


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
