"""Auth/RBAC tests (Section AC)."""
import pytest
from fastapi import HTTPException

from app.core import auth as A
from app.core.auth import (
    Role, create_token, decode_token, issue_pair, require_role, rotate_refresh,
)
from app.core.config import Settings


def test_token_roundtrip_rs256():
    t = create_token("user-1", Role.ANALYST, "access")
    p = decode_token(t)
    assert p["sub"] == "user-1" and p["role"] == "ANALYST" and p["type"] == "access"


async def test_open_mode_returns_synthetic_superadmin(monkeypatch):
    monkeypatch.setattr(A, "get_settings", lambda: Settings(require_auth=False))
    pr = await require_role(Role.SUPERADMIN)(authorization=None)
    assert pr.role == Role.SUPERADMIN


async def test_enforced_role_hierarchy(monkeypatch):
    monkeypatch.setattr(A, "get_settings", lambda: Settings(require_auth=True))
    with pytest.raises(HTTPException):                       # no token
        await require_role(Role.ANALYST)(authorization=None)
    read_only = "Bearer " + create_token("u", Role.READ_ONLY)
    with pytest.raises(HTTPException):                       # insufficient role
        await require_role(Role.ANALYST)(authorization=read_only)
    analyst = "Bearer " + create_token("u", Role.ANALYST)
    pr = await require_role(Role.ANALYST)(authorization=analyst)
    assert pr.role == Role.ANALYST


async def test_refresh_token_rejected_for_resource_access(monkeypatch):
    monkeypatch.setattr(A, "get_settings", lambda: Settings(require_auth=True))
    pair = issue_pair("u", Role.ADVISOR)
    with pytest.raises(HTTPException):
        await require_role(Role.READ_ONLY)(authorization="Bearer " + pair["refresh_token"])


def test_refresh_rotation_is_single_use():
    pair = issue_pair("u", Role.SUPERADMIN)
    new = rotate_refresh(pair["refresh_token"])
    assert "access_token" in new and "refresh_token" in new
    with pytest.raises(HTTPException):
        rotate_refresh(pair["refresh_token"])  # already rotated


def test_m2m_token_carries_role_and_type():
    t = create_token("m2m:bot", Role.READ_ONLY, token_type="m2m")
    p = decode_token(t)
    assert p["type"] == "m2m" and p["role"] == "READ_ONLY"
