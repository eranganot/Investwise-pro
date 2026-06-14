"""Section AC - JWT (RS256) auth, RBAC roles, refresh rotation, M2M tokens.

Enforcement is gated by `require_auth`. When off (the demo default), protected
routes resolve to a synthetic SUPERADMIN principal so the app stays open; when
on, a valid Bearer JWT with sufficient role is required.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from functools import lru_cache

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Header, HTTPException, Request
from pydantic import BaseModel

from app.core.config import get_settings


class Role(str, Enum):
    SUPERADMIN = "SUPERADMIN"
    ADVISOR = "ADVISOR"
    ANALYST = "ANALYST"
    READ_ONLY = "READ_ONLY"


ORDER = {Role.READ_ONLY: 0, Role.ANALYST: 1, Role.ADVISOR: 2, Role.SUPERADMIN: 3}


class Principal(BaseModel):
    sub: str
    role: Role
    token_type: str = "access"


@lru_cache
def _keys() -> tuple[bytes, bytes]:
    s = get_settings()
    if s.jwt_private_key and s.jwt_public_key:
        return s.jwt_private_key.encode(), s.jwt_public_key.encode()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)  # ephemeral (dev)
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    return priv, pub


def create_token(sub: str, role: Role, token_type: str = "access", ttl: int | None = None) -> str:
    s = get_settings()
    default_ttl = {"access": s.access_token_ttl_sec, "refresh": s.refresh_token_ttl_sec,
                   "m2m": s.m2m_token_ttl_sec}.get(token_type, s.access_token_ttl_sec)
    now = int(time.time())
    payload = {"sub": sub, "role": role.value, "type": token_type,
               "iat": now, "exp": now + (ttl or default_ttl), "jti": uuid.uuid4().hex}
    return jwt.encode(payload, _keys()[0], algorithm="RS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, _keys()[1], algorithms=["RS256"])


def issue_pair(sub: str, role: Role) -> dict:
    return {"access_token": create_token(sub, role, "access"),
            "refresh_token": create_token(sub, role, "refresh"),
            "token_type": "bearer", "role": role.value}


def _principal_from_header(authorization: str | None) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        payload = decode_token(authorization.split(" ", 1)[1])
    except Exception:  # noqa: BLE001
        raise HTTPException(401, "invalid or expired token")
    if payload.get("type") == "refresh":
        raise HTTPException(401, "refresh token cannot access resources")
    return Principal(sub=payload["sub"], role=Role(payload.get("role", "READ_ONLY")),
                     token_type=payload.get("type", "access"))


def _principal_from_token(token: str) -> Principal:
    try:
        payload = decode_token(token)
    except Exception:  # noqa: BLE001
        raise HTTPException(401, "invalid or expired token")
    if payload.get("type") == "refresh":
        raise HTTPException(401, "refresh token cannot access resources")
    return Principal(sub=payload["sub"], role=Role(payload.get("role", "READ_ONLY")),
                     token_type=payload.get("type", "access"))


def require_role(min_role: Role):
    async def _dep(request: Request = None, authorization: str | None = Header(default=None)) -> Principal:
        s = get_settings()
        if not s.require_auth:
            return Principal(sub=s.superadmin_name, role=Role.SUPERADMIN)
        # token from Authorization header (API clients) or the session cookie (browser).
        token = None
        if authorization and authorization.startswith("Bearer "):
            token = authorization.split(" ", 1)[1]
        elif request is not None:
            token = request.cookies.get(s.session_cookie_name)
        if not token:
            raise HTTPException(401, "not authenticated")
        principal = _principal_from_token(token)
        # Email allowlist: user tokens must be allow-listed (m2m tokens exempt).
        allowed = s.allowed_email_list
        if allowed and "@" in principal.sub and principal.sub.lower() not in allowed:
            raise HTTPException(403, "this account is not allowed")
        if ORDER[principal.role] < ORDER[min_role]:
            raise HTTPException(403, f"requires role {min_role.value} or higher")
        return principal
    return _dep
