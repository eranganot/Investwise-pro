"""PR 104c.2: keep Today warm.

Eran, 2026-10-07: "it takes very long time for the goals, holdings prices, and
cards recommendations to upload". Measured on production right after a deploy:
the first `/recommendations` took 22.2 s -- buy ideas 15.8 s (the screener
fetching a year of history per universe ticker), war room 3.5 s -- and the next
one 0.9 s. The work is the same; only the caches were cold. A deploy empties
them, and the history tier forgets after an hour.

So the server builds every user's Today once shortly after it starts, and again
every 45 minutes (inside the history cache's hour), in the app's OWN event loop
with its own sessions -- no cross-loop engine (CLAUDE.md, "Cross-loop sessions").
Building Today has no side effect beyond what opening Today has: it sends no
notification.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

FIRST_DELAY_S = 20.0
EVERY_S = 45 * 60.0


async def warm_once(session_factory=None) -> int:
    """Build Today for every user once. Returns how many builds succeeded.

    `session_factory` defaults to the app's; a caller in ANOTHER event loop (a
    test) passes its own, per the cross-loop rule."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.tables import User
    from app.services.recommendations import build_recommendations

    AsyncSessionLocal = session_factory or AsyncSessionLocal  # noqa: N806
    async with AsyncSessionLocal() as s:
        ids = list((await s.scalars(select(User.id))).all())
    done = 0
    for uid in ids:
        try:
            async with AsyncSessionLocal() as s:
                user = await s.get(User, uid)
                if user is None:
                    continue
                await build_recommendations(s, user)
                done += 1
        except Exception:  # noqa: BLE001 -- a warm-up never takes the app down
            logger.warning("warm-up build failed for one user", exc_info=True)
    logger.info("warm-up: built Today for %d user(s)", done)
    return done


async def warm_loop(first_delay: float = FIRST_DELAY_S, every: float = EVERY_S) -> None:
    await asyncio.sleep(first_delay)
    while True:
        try:
            await warm_once()
        except Exception:  # noqa: BLE001
            logger.warning("warm-up pass failed", exc_info=False)
        await asyncio.sleep(every)
