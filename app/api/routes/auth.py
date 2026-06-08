"""Auth endpoints (Section AC)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import Principal, Role, create_token, issue_pair, require_role, rotate_refresh
from app.core.config import get_settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/token")
async def login(req: LoginRequest) -> dict:
    s = get_settings()
    if req.email.lower() == "eran.ganot@gmail.com" and req.password == s.auth_password:
        return issue_pair(req.email, Role.SUPERADMIN)
    raise HTTPException(401, "invalid credentials")


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh")
async def refresh(req: RefreshRequest) -> dict:
    return rotate_refresh(req.refresh_token)


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
