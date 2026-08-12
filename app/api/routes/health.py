import os

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import engine

router = APIRouter(tags=["health"])
settings = get_settings()


def deployed_commit() -> str:
    """Which commit is actually serving this request.

    `settings.app_version` is a hand-edited string ("22.1"); it says what
    somebody last typed, not what is running. Railway injects the real thing.

    This exists because STATUS records two debugging rounds lost to a smoke run
    against a stale container, and every ship script still says "confirm
    Railway shows $sha Active BEFORE believing any smoke result" -- a check that
    could only be done by eye, in the dashboard. Phase B's own verification hit
    it again: the after-measurement could not be trusted until a screenshot
    confirmed the deploy. One field turns that into a question the API answers.

    Reading the environment rather than git: the Dockerfile copies `app`,
    `alembic` and two files -- there is no `.git` in the image to ask.
    """
    for var in ("RAILWAY_GIT_COMMIT_SHA", "GIT_COMMIT_SHA", "SOURCE_COMMIT"):
        sha = os.environ.get(var)
        if sha:
            return sha[:7]
    # Honest about not knowing, rather than reporting a version that would be
    # mistaken for the commit -- which is the whole failure mode being fixed.
    return "unknown"


@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "commit": deployed_commit(),
        "environment": settings.environment,
    }


@router.get("/health/ready")
async def ready() -> dict:
    """Readiness probe - verifies the database is reachable."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"database not ready: {exc}")
    return {"status": "ready", "database": "ok"}
