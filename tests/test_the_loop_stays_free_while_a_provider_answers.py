"""One slow provider call must not stop the app answering anybody else.

`app/core/offload.py` was written for exactly this fault and already records the
measurement that motivated it, taken against production:

    /plan on its own                                  0.38 - 0.52 s
    /plan issued while /recommendations was in flight 2.09 / 2.22 / 3.05 s

Nothing was slow. /plan was queued behind a synchronous provider call running
on the event loop, and finished the instant the loop was released.

That sweep moved the *agents* off the loop and missed the provider calls made
directly from `async def` handlers and services. Reproduced here on 2026-09-04
against `6f3cc4f`, in-process, with the market provider patched to take 2.0s:

     quote:  2.00s  HTTP 200
    health:  2.01s  HTTP 200
    BLOCKED: /health waited 2.01s behind a 2.0s provider call.

RULED OUT -- "httpx's ASGITransport serialises requests, so this measures the
harness". A control run put the same 2.0s wait behind `await asyncio.sleep`
instead of `time.sleep`, in a handler on the same transport:

      awaited: slow  2.01s   /health  0.00s
     blocking: slow  2.00s   /health  2.01s

The transport is concurrent. The loop was not. `test_the_harness_is_concurrent`
below is that control, kept in the suite so a future httpx cannot quietly turn
this file into a tautology.

Thread safety is not a new question here: `recommendations.py` has offloaded
`_buy_ideas` -- which hits the same providers, through the same circuit
breakers -- since #15 Phase B, so provider calls already run on worker threads
in production.

Not financial advice.
"""
import ast
import asyncio
import os
import pathlib
import time

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("AUTO_CREATE_TABLES", "true")

BLOCK = 1.0
FREE = BLOCK / 2          # anything under this means the loop was never held

REPO = pathlib.Path(__file__).resolve().parent.parent


class _Quote:
    price = 1.0
    currency = "USD"

    def model_dump(self):
        return {"ticker": "VTI", "price": 1.0}


def _slow_quote(ticker):
    time.sleep(BLOCK)
    return _Quote()


async def _race(app, slow_url, method="GET", json=None):
    """Issue `slow_url` and /health together. Return when each finished."""
    import httpx

    marks: dict[str, float] = {}
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        await c.get("/health")            # pay the startup cost outside the clock
        t0 = time.perf_counter()

        async def hit(name, url, meth="GET", body=None):
            await c.request(meth, url, json=body)
            marks[name] = time.perf_counter() - t0

        await asyncio.gather(hit("slow", slow_url, method, json), hit("health", "/health"))
    return marks


async def test_the_harness_is_concurrent():
    """The control. If this fails, the test below proves nothing.

    A handler that AWAITS its two seconds must not delay /health. Should httpx
    ever start serialising requests over ASGITransport, this fails first and
    says so, instead of leaving the real test passing for the wrong reason.
    """
    from fastapi import APIRouter

    import app.main as m

    r = APIRouter()

    @r.get("/__control/slow_async")
    async def _slow_async():
        await asyncio.sleep(BLOCK)
        return {"ok": True}

    m.app.include_router(r)
    try:
        marks = await _race(m.app, "/__control/slow_async")
    finally:
        # A control route left on the shared app is a public route to every
        # later test (PR 112: test_no_new_route_is_public_by_accident saw it).
        m.app.router.routes[:] = [x for x in m.app.router.routes
                                  if getattr(x, "path", "") != "/__control/slow_async"]

    assert marks["slow"] >= BLOCK * 0.9, marks
    assert marks["health"] < FREE, (
        f"/health waited {marks['health']:.2f}s behind a handler that was only "
        f"awaiting -- the transport is serialising, so nothing else in this "
        f"file is measuring the app")


async def test_a_slow_quote_does_not_stall_every_other_request():
    """The fault itself, at the endpoint where it is easiest to see.

    `guarded_quote` is a plain `def` that makes a synchronous network call. On
    one uvicorn worker, calling it straight from an `async def` route holds the
    loop for the whole round trip and every other request waits.

    PR 112: raced through POST /portfolio/add (Holdings > add by amount), the
    live route that quotes; the legacy GET /market/quote it used is deleted.
    """
    import app.api.routes.intake as intake
    import app.main as m

    original = intake.guarded_quote
    intake.guarded_quote = _slow_quote
    try:
        marks = await _race(m.app, "/api/v1/portfolio/add", method="POST",
                            json={"ticker": "VTI", "amount": 100})
    finally:
        intake.guarded_quote = original

    assert marks["slow"] >= BLOCK * 0.9, (
        f"the provider was supposed to take {BLOCK}s: {marks}")
    assert marks["health"] < FREE, (
        f"/health took {marks['health']:.2f}s while one quote was in flight. "
        f"The provider call is running on the event loop, so a single slow "
        f"upstream is an outage for every other endpoint.")


# --------------------------------------------------------------------------
# The guard. Behaviour above proves the endpoint; this proves the whole tree,
# because the last time I fixed a class of bug by hand I found the call sites
# with a grep piped through `head`, believed the ten lines it printed, and
# shipped a fix that changed nothing.
# --------------------------------------------------------------------------

BLOCKING = {"guarded_quote", "guarded_history", "guarded_fundamentals",
            "guarded_fx", "guarded_events"}


def _is_research_scan(node: ast.Call) -> bool:
    """`ResearchAgent().scan()` -- and NOT `LagEngine().scan(observations)`.

    Matching on the method name alone is how the first sweep produced four
    false positives: `lag.scan()` is pure arithmetic over a list already in
    memory and belongs on the loop.
    """
    f = node.func
    return (isinstance(f, ast.Attribute) and f.attr == "scan"
            and isinstance(f.value, ast.Call)
            and isinstance(f.value.func, ast.Name)
            and f.value.func.id == "ResearchAgent")


def _offloaded(fn: ast.AST) -> set[str]:
    """Names handed TO offload() -- `await offload(guarded_quote, tk)` is fine."""
    ok: set[str] = set()
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        name = n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", None)
        if name not in {"offload", "_offload"}:
            continue
        for a in n.args:
            if isinstance(a, ast.Name):
                ok.add(a.id)
            elif isinstance(a, ast.Attribute):
                ok.add(a.attr)
    return ok


def test_no_async_function_calls_a_provider_on_the_loop():
    offenders = []
    for path in sorted((REPO / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            safe = _offloaded(fn)
            for n in ast.walk(fn):
                if not isinstance(n, ast.Call):
                    continue
                name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
                if name in BLOCKING and name not in safe:
                    offenders.append(
                        f"{path.relative_to(REPO)}:{n.lineno} in async def "
                        f"{fn.name}() calls {name}()")
                elif _is_research_scan(n) and "scan" not in safe:
                    offenders.append(
                        f"{path.relative_to(REPO)}:{n.lineno} in async def "
                        f"{fn.name}() calls ResearchAgent().scan()")

    assert not offenders, (
        "these run a synchronous network call on the event loop, so while each "
        "one waits the app answers nobody:\n  " + "\n  ".join(offenders)
        + "\n\nHand it to a worker thread: `await offload(fn, *args)`.")
