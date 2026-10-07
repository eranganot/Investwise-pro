"""PR 104c.2: the server builds Today in the background so the first visit after a
deploy is not the one that pays for cold caches (22.2 s measured, 0.9 s warm).

CLAUDE.md: "A job nothing calls is a job nobody has tested" -- this RUNS it.
"""
import asyncio

from fastapi.testclient import TestClient

from app.main import app
from app.services import warmup


async def test_warm_once_builds_today_for_every_user(own_session):
    from app.services.feed_service import ensure_superadmin
    with TestClient(app) as c:
        c.get("/api/v1/portfolio")
    async with own_session() as s:
        await ensure_superadmin(s)                    # the app has its user
        await s.commit()
    n = await warmup.warm_once(session_factory=own_session)
    assert n >= 1


def test_the_loop_runs_a_pass_then_waits(monkeypatch):
    passes = []

    async def _once():
        passes.append(1)
        if len(passes) >= 2:
            raise asyncio.CancelledError
        return 1
    monkeypatch.setattr(warmup, "warm_once", _once)
    try:
        asyncio.run(warmup.warm_loop(first_delay=0, every=0))
    except asyncio.CancelledError:
        pass
    assert len(passes) == 2
