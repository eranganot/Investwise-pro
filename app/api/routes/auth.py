"""Auth endpoints (Section AC) - DB-backed credentials + refresh rotation."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, Role, create_token, issue_pair, require_role
from app.core.database import get_session
from app.services.auth_service import (
    ensure_superadmin_credential, rotate_refresh, verify_login,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/token")
async def login(req: LoginRequest, session: AsyncSession = Depends(get_session)) -> dict:
    await ensure_superadmin_credential(session)
    await session.commit()
    role = await verify_login(session, req.email, req.password)
    if role is None:
        raise HTTPException(401, "invalid credentials")
    return issue_pair(req.email.lower(), role)


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh")
async def refresh(req: RefreshRequest, session: AsyncSession = Depends(get_session)) -> dict:
    return await rotate_refresh(session, req.refresh_token)


class M2MRequest(BaseModel):
    client: str
    role: Role = Role.READ_ONLY


@router.post("/m2m")
async def issue_m2m(req: M2MRequest,
                    principal: Principal = Depends(require_role(Role.SUPERADMIN))) -> dict:
    token = create_token(f"m2m:{req.client}", req.role, token_type="m2m")
    return {"m2m_token": token, "role": req.role.value, "client": req.client, "token_type": "bearer"}


@router.get("/me")
async def me(principal: Principal = Depends(require_role(Role.READ_ONLY))) -> dict:
    return principal.model_dump()
