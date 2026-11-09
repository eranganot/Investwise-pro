"""Section AC - JWT (RS256) auth, RBAC roles, refresh rotation, M2M tokens.

Enforcement is gated by `require_auth`. When off (the demo default), protected
routes resolve to a synthetic SUPERADMIN principal so the app stays open; when
on, a valid Bearer JWT with sufficient role is required.
"""
from __future__ import annotations

import hmac
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
    jti: str | None = None


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


def _alg() -> tuple[str, object, object]:
    """Return (algorithm, sign_key, verify_key). A configured ``jwt_secret`` gives
    HS256 with a stable key (sessions survive redeploys); otherwise an ephemeral
    RS256 keypair is used (sessions reset on restart)."""
    s = get_settings()
    if s.jwt_secret:
        return "HS256", s.jwt_secret, s.jwt_secret
    priv, pub = _keys()
    return "RS256", priv, pub


def create_token(sub: str, role: Role, token_type: str = "access", ttl: int | None = None) -> str:
    s = get_settings()
    default_ttl = {"access": s.access_token_ttl_sec, "refresh": s.refresh_token_ttl_sec,
                   "m2m": s.m2m_token_ttl_sec}.get(token_type, s.access_token_ttl_sec)
    now = int(time.time())
    payload = {"sub": sub, "role": role.value, "type": token_type,
               "iat": now, "exp": now + (ttl or default_ttl), "jti": uuid.uuid4().hex}
    alg, sign_key, _ = _alg()
    return jwt.encode(payload, sign_key, algorithm=alg)


def decode_token(token: str) -> dict:
    alg, _, verify_key = _alg()
    return jwt.decode(token, verify_key, algorithms=[alg])


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
                     token_type=payload.get("type", "access"), jti=payload.get("jti"))


# --- logout ends THIS browser's session (PR 111, D73) -------------------------
#
# `/auth/logout` used to delete the cookie and nothing else, so the token inside
# it stayed valid for its full 30 days: a copied cookie or bearer replayed after
# logout got 200 (S2). Logout now writes the token's `jti` to `revoked_tokens`
# (the table refresh-token rotation already uses), and every authenticated
# request checks it. Owner's choice (D-f option 2): only the browser you log out
# of is signed out; other devices keep their sessions.
#
# PR 111.1: IN MEMORY, LOADED AT BOOT -- no database read in the auth path.
# 111 checked `revoked_tokens` per request (cached 60 s). On Postgres that put a
# pooled connection into every authenticated request, including tests that
# drive the app without its lifespan, and asyncpg left `Connection._cancel`
# un-awaited across loops (the CLAUDE.md cross-loop trap; CI warning on
# test_rbac_enforced_when_auth_on). The app is one process: logout adds the jti
# here and to the table; boot reloads the table (`load_revoked`), so a restart
# forgets nothing.
_REVOKED: set[str] = set()


def mark_revoked(jti: str) -> None:
    _REVOKED.add(jti)


def is_revoked(jti: str | None) -> bool:
    return bool(jti) and jti in _REVOKED


async def load_revoked(session) -> int:
    """Reload every revoked jti from the database. Called once at startup."""
    from sqlalchemy import select

    from app.models.tables import RevokedToken
    jtis = (await session.scalars(select(RevokedToken.jti))).all()
    _REVOKED.clear()
    _REVOKED.update(j for j in jtis if j)
    return len(_REVOKED)


def require_role(min_role: Role):
    async def _dep(request: Request = None, authorization: str | None = Header(default=None)) -> Principal:
        s = get_settings()
        # Agent override: a request carrying the secret X-Agent-Key acts as SUPERADMIN
        # (attributed to the owner) even when auth is enforced - lets the agent apply
        # holdings overrides without disabling auth.
        # D-1: the scheduled smoke's key reads, and only reads. It acts as the owner (so the
        # smoke sees his book; `acting_user` records the principal's role on the user row, so
        # it must not be a lower role) but any method other than GET/HEAD is refused.
        _sent = request.headers.get("x-agent-key") if request is not None else None
        if _sent and s.smoke_api_key and s.smoke_api_key != s.agent_api_key \
                and hmac.compare_digest(_sent, s.smoke_api_key):
            if request.method not in ("GET", "HEAD"):
                raise HTTPException(403, "the smoke key is read-only")
            return Principal(sub=s.superadmin_email, role=Role.SUPERADMIN)
        if s.agent_api_key and request is not None:
            # IW-5: constant-time, like the smoke key above.
            if _sent and hmac.compare_digest(_sent, s.agent_api_key):
                return Principal(sub=s.superadmin_email, role=Role.SUPERADMIN)
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
        if is_revoked(principal.jti):
            raise HTTPException(401, "this session was logged out")
        # Email allowlist: user tokens must be allow-listed (m2m tokens exempt).
        allowed = s.allowed_email_list
        if allowed and "@" in principal.sub and principal.sub.lower() not in allowed:
            raise HTTPException(403, "this account is not allowed")
        if ORDER[principal.role] < ORDER[min_role]:
            raise HTTPException(403, f"requires role {min_role.value} or higher")
        return principal
    return _dep
