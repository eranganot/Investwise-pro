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


async def schema_revision() -> str | None:
    """Which alembic revision this database is actually stamped at.

    The companion to `deployed_commit()`, and for the same reason: the deploy's
    schema state could only be checked by eye, and in this app it could not be
    checked at all. Running alembic by hand against production failed twice --
    once against the sqlite URL in a local `.env`, once on DNS, because
    Railway's `DATABASE_URL` names a private host that does not resolve from
    outside their network. The consequence is written across `main.py`: a
    hand-maintained list of `ALTER TABLE ... IF NOT EXISTS` statements became
    "the mechanism that keeps production's schema current", and twice a
    migration added a column that never reached it -- `last_error` (0011) and
    `benchmark_ticker` (0015), the second of which silently returned "no
    strategy has ever been measured" for ten days.

    Returning `None` is a real answer and the one production gave before this:
    **the migrations have never run here.** It is not an error, so it never
    raises -- a liveness probe that can fail on a side query is worse than the
    thing it reports.
    """
    try:
        async with engine.connect() as conn:
            row = await conn.execute(text("SELECT version_num FROM alembic_version"))
            value = row.scalar()
            return str(value) if value else None
    except Exception:  # noqa: BLE001 - no table, no database, no answer
        return None


@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "commit": deployed_commit(),
        "environment": settings.environment,
        # None means the migrations have never run against this database.
        "schema_revision": await schema_revision(),
        # C8 / PR 57. `schema_revision` says where the migrations got to; this
        # says whether anything ELSE is also writing the schema. Both matter,
        # and only together: a revision at head while the app also runs
        # `create_all` at boot cannot distinguish "the migrations built this"
        # from "the models did, and the migrations are along for the ride".
        # False is the correct answer in production. Exposed rather than merely
        # logged because a log line is something you have to go and look for,
        # and this one would only be interesting on the day nobody looked.
        "builds_own_schema": settings.auto_create_tables,
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
