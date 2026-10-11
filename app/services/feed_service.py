"""User rows: `ensure_user` / `ensure_superadmin`. (The decision-feed mapper that
also lived here was deleted with that subsystem in PR 113.)"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.tables import User

SUPERADMIN_EMAIL = "eran.ganot@gmail.com"

async def ensure_user(session: AsyncSession, email: str, *, name: str | None = None,
                      role: str = "SuperAdmin") -> User:
    res = await session.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if user is None:
        user = User(email=email, name=name or email, role=role,
                    tax_year=get_settings().tax_year)
        session.add(user)
        await session.flush()
    return user


async def ensure_superadmin(session: AsyncSession) -> User:
    return await ensure_user(session, SUPERADMIN_EMAIL,
                             name=get_settings().superadmin_name, role="SuperAdmin")
