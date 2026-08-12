"""Run blocking work off the event loop (#15, Phase B).

The app makes SYNCHRONOUS network calls -- urllib, via `guarded_*` and the
Gemini REST helper -- from inside `async def` handlers, on a single uvicorn
worker. While one of those runs, the loop cannot serve anybody else.

Measured against production before this existed:

    /plan on its own                                  0.38 - 0.52 s
    /plan issued while /recommendations was in flight 2.09 / 2.22 / 3.05 s

Nothing was slow. /plan was queued, and it completed the instant the loop was
released -- in every trial it finished just before the /recommendations call
that was holding the loop.

One helper rather than `asyncio.to_thread` sprinkled at each site, so there is
one place to change if this ever needs a bounded executor instead of the
default one. `to_thread` uses the loop's default ThreadPoolExecutor, which is
unbounded-ish (min(32, cpu+4) workers); that is fine for a handful of agents
per request and would need revisiting if this became per-provider-call.

What this does NOT do: make a single request faster. Each caller still waits
for its own work. It stops that work from being everyone else's problem.
"""
from __future__ import annotations

import asyncio
from typing import Callable, TypeVar

T = TypeVar("T")


async def offload(fn: Callable[..., T], *args, **kwargs) -> T:
    """Await `fn(*args, **kwargs)` on a worker thread.

    Exceptions propagate to the caller exactly as if it had been called
    directly, so every existing defensive `try/except` around these agents
    keeps working unchanged.
    """
    return await asyncio.to_thread(fn, *args, **kwargs)
