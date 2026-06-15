"""API-key auth (optional).

If `api_key` (env API_KEY) is empty, auth is disabled - the demo/dev default.
When set, write endpoints require a matching `X-API-Key` header.
"""
from __future__ import annotations

from fastapi import Header, HTTPException

from app.core.config import get_settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if not settings.api_key:
        return  # auth disabled
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
