"""Phase B (#15): blocking provider work must not hold the event loop.

The defect these pin was measured against production, not theorised:

    /plan on its own                                  0.38 - 0.52 s
    /plan issued while /recommendations was in flight 2.09 / 2.22 / 3.05 s

Nothing was slow. /plan was queued behind synchronous urllib running inside an
`async def` on a single uvicorn worker, and it finished the instant the loop
was released -- in every trial, just before the /recommendations call that had
been holding it.

These tests use a fake blocking agent rather than a real provider, so they are
deterministic and make no network call.

**No assertion here may put a lower bound on a wall-clock `time.sleep`.**
Windows' platform timer ticks at 15.625ms, so `time.sleep(0.30)` returns after
19 ticks -- 296.875ms, three milliseconds SHORT of what was asked for. An
`assert total >= 0.30` therefore fails on Windows every single run while passing
on the Linux CI box forever. Bound the duration well below the requested sleep,
or assert on ordering instead: both readings come off the same clock, so their
relative order is exact no matter how coarse it is.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.core.offload import offload


def _blocking_agent(seconds: float = 0.30) -> str:
    """Stands in for guarded_history/_momentum_recs: a synchronous sleep is
    exactly what a synchronous socket read looks like to the event loop."""
    time.sleep(seconds)
    return "done"


@pytest.mark.asyncio
async def test_offload_leaves_the_loop_free_to_serve_others():
    """The whole point of #15, as one assertion.

    A cheap coroutine is started while the blocking agent runs. If the agent
    held the loop, the cheap one could not finish until it was done.
    """
    cheap_finished_at = None
    started = time.monotonic()

    async def cheap():
        nonlocal cheap_finished_at
        await asyncio.sleep(0.02)
        cheap_finished_at = time.monotonic() - started

    slow = asyncio.create_task(offload(_blocking_agent, 0.30))
    await asyncio.sleep(0)          # let the offload actually start
    await cheap()
    assert await slow == "done"

    total = time.monotonic() - started
    assert cheap_finished_at is not None
    # The cheap coroutine must have finished near its own 0.02s, NOT after the
    # 0.30s agent. Generous bound so a loaded CI box cannot flake it.
    assert cheap_finished_at < 0.20, (
        f"the loop was blocked: a 0.02s coroutine took {cheap_finished_at:.3f}s"
    )
    # ...and the slow work really did run. Asserted two ways, neither of which
    # is `total >= 0.30`:
    #
    # `await slow == "done"` above already proves the agent ran to completion --
    # it only returns after its sleep. This adds that it OUTLASTED the cheap
    # coroutine, which is the ordering the test is really about, and that it
    # actually slept rather than returning immediately.
    #
    # The floor is 0.25, not 0.30, and that is not slop: `assert total >= 0.30`
    # failed on Windows at 0.29699999999138527. `time.sleep(0.30)` there returns
    # after 19 platform ticks of 15.625ms = 296.875ms, so the strict bound is
    # unsatisfiable on that platform and green on Linux forever. 0.25 still
    # separates "it slept" from "it did not" by an order of magnitude.
    assert total > cheap_finished_at
    assert total >= 0.25, f"the agent did not sleep: {total:.3f}s"


@pytest.mark.asyncio
async def test_calling_the_agent_directly_does_block_the_loop():
    """The control. Without offload the same code holds the loop.

    Without this, the test above could pass for the wrong reason -- if the fake
    agent were not actually blocking, "the loop stayed free" would prove
    nothing.
    """
    cheap_finished_at = None
    started = time.monotonic()

    async def cheap():
        nonlocal cheap_finished_at
        await asyncio.sleep(0.02)
        cheap_finished_at = time.monotonic() - started

    task = asyncio.create_task(cheap())
    _blocking_agent(0.30)          # called directly, on the loop's own thread
    await task

    assert cheap_finished_at is not None
    # 0.25 for the same reason as the sibling test: a wall-clock sleep can come
    # back short of what was asked for, and 0.30 is not a bound Windows can meet.
    #
    # This one survived the Windows run only by accident -- `cheap()` is created
    # but never started before the blocking call, so its own 0.02s lands on TOP
    # of the short sleep and pushes the total back over 0.30. One
    # `await asyncio.sleep(0)` added here for tidiness would have failed it too.
    #
    # No discriminating power is lost: the two outcomes this separates are
    # ~0.02s (loop stayed free) and ~0.30s (loop was held).
    assert cheap_finished_at >= 0.25, (
        "the fake agent is not actually blocking, so the sibling test proves nothing"
    )


@pytest.mark.asyncio
async def test_offloaded_failures_propagate_unchanged():
    """Every one of these agents sits inside a defensive try/except in
    build_recommendations. If offload swallowed or re-wrapped exceptions, those
    handlers would stop catching and a data hiccup would break Today."""
    def boom():
        raise ValueError("provider said no")

    with pytest.raises(ValueError, match="provider said no"):
        await offload(boom)


@pytest.mark.asyncio
async def test_two_offloaded_agents_overlap():
    """Agents run concurrently rather than one after another."""
    started = time.monotonic()
    await asyncio.gather(offload(_blocking_agent, 0.25),
                         offload(_blocking_agent, 0.25))
    elapsed = time.monotonic() - started
    assert elapsed < 0.45, f"serialized: {elapsed:.2f}s for two 0.25s agents"


def test_the_app_session_does_not_expire_on_commit():
    """ORM safety, part 1 -- pinned against the APP's configuration.

    The offloaded agents receive live ORM `rows`. Reading an EXPIRED attribute
    from a worker thread would lazy-load through the async session and raise
    MissingGreenlet. Nothing expires today because of two decisions that are
    easy to reverse without realising what now depends on them:

      * AsyncSessionLocal sets expire_on_commit=False   <- asserted here
      * _agent_tx uses a SAVEPOINT, because session.rollback() expires
        everything (its own docstring already records that lesson)

    This asserts the real sessionmaker rather than a locally-built one on
    purpose. Every session fixture in this suite passes expire_on_commit=False
    itself, so a test that built its own would keep passing after someone
    flipped the app's setting -- proving only that the fixture was configured
    correctly.
    """
    from app.core.database import AsyncSessionLocal

    assert AsyncSessionLocal.kw.get("expire_on_commit") is False, (
        "expire_on_commit is back on: offloaded agents would lazy-load ORM "
        "attributes from a worker thread and raise MissingGreenlet"
    )


@pytest.mark.asyncio
async def test_loaded_positions_can_be_read_from_a_worker_thread():
    """ORM safety, part 2 -- actually do it.

    Throwaway NullPool engine in this test's own event loop, per the repo's
    Postgres isolation rule (borrowing the app engine across loops is what
    asyncpg rejects).
    """
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import app.models.tables  # noqa: F401  register tables
    from app.core.config import get_settings
    from app.core.database import AsyncSessionLocal
    from app.models.base import Base
    from app.schemas.intake import IntakePosition
    from app.services.feed_service import ensure_superadmin
    from app.services.intake_service import (
        ensure_account, ensure_entity, list_positions, upsert_positions,
    )

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # Mirror the app's own setting rather than hardcoding it, so this can
        # never disagree with production about the thing it is testing.
        Session = async_sessionmaker(
            engine, expire_on_commit=AsyncSessionLocal.kw.get("expire_on_commit"))
        async with Session() as s:
            user = await ensure_superadmin(s)
            # Built explicitly rather than walked via user.entities[0] -- that
            # is a RELATIONSHIP, and traversing it lazy-loads. It raised
            # MissingGreenlet here on the first run, which is a fair preview of
            # what an offloaded agent would hit if it ever touched one.
            entity = await ensure_entity(s, user, "Personal", "Personal")
            account = await ensure_account(s, entity, "Main")
            await upsert_positions(s, account, [
                IntakePosition(ticker="TQQQ", market="NASDAQ", spot_price=60.0,
                               listing_price=60.0, quantity=10, cost_basis=50.0)])
            await s.commit()        # the commit that must NOT expire anything

            rows = await list_positions(s, user)
            assert rows

            # COLUMNS must all be loaded -- that is what expire_on_commit
            # governs, and what the offloaded agents actually read.
            #
            # RELATIONSHIPS are a different matter and the first run of this
            # test is what made the distinction concrete: `unloaded` came back
            # as {'account', 'transactions'}. Relationships are lazy by default
            # and stay unloaded no matter what commit does, so touching one
            # from a worker thread raises MissingGreenlet -- asserted below.
            # The rule for anything passed to `offload` is therefore: columns
            # yes, relationships never.
            for row in rows:
                unloaded = sa_inspect(row).unloaded
                columns = {c.key for c in sa_inspect(type(row)).column_attrs}
                unloaded_columns = unloaded & columns
                assert not unloaded_columns, (
                    f"{row.ticker} has unloaded COLUMNS {sorted(unloaded_columns)} "
                    "-- reading them on a worker thread would raise MissingGreenlet"
                )

            def read_on_worker(positions):
                return [(p.ticker, float(p.quantity)) for p in positions]

            assert await offload(read_on_worker, rows) == [("TQQQ", 10.0)]

            # The other half of the rule, proven rather than asserted in a
            # comment: a relationship is NOT safe to touch off-thread.
            #
            # The Account has to be expunged first. SQLAlchemy resolves a
            # many-to-one from the identity map WITHOUT emitting SQL, and this
            # test created the Account in this very session -- so the first
            # version of this assertion passed happily and proved nothing. A
            # real request does not have it cached.
            s.expunge(account)
            def touch_relationship_on_worker(positions):
                return positions[0].account

            with pytest.raises(Exception) as caught:
                await offload(touch_relationship_on_worker, rows)
            assert "MissingGreenlet" in type(caught.value).__name__ or \
                   "greenlet_spawn" in str(caught.value), (
                f"expected a lazy-load failure, got {caught.value!r}")
    finally:
        await engine.dispose()
