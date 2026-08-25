"""C1 -- a sleeve becomes a row, and nothing else moves.

``plans.strategy`` + ``plans.strategy_sleeve_pct`` could hold exactly one
strategy. Applying a second one overwrote the first, which is why the app looked
like it had two strategies when it had one. C1 added the table that holds N of
them, backfilled the one that already existed, and deliberately stopped there:
``apply_strategy`` went on writing the old columns and the rest of the app went
on reading them, so a rollback would not lose the applied strategy.

**That window is closed.** Migration 0019 dropped both columns and deleted both
one-shot backfills, so the half of this file that proved "the old path is
untouched" is now proving something that does not exist. It has been replaced,
not deleted: the assertions now say the old path is GONE and that nothing grew
back -- which is the property that matters once there is only one answer.

The core is the IMPLICIT REMAINDER in this model: sleeves sum to <= 100 and what
is left stays objective-managed unless a core strategy is chosen (C6).
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app.models.tables import PlanSleeve
from app.services import sleeve_service as sv


class _Fake:
    """A sleeve without a database, so the pure invariants stay pure."""

    def __init__(self, strategy_id, sleeve_pct, is_core=False):
        self.strategy_id = strategy_id
        self.sleeve_pct = sleeve_pct
        self.is_core = is_core


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "V", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
         "spot_price": 365, "listing_price": 365, "quantity": 10, "cost_basis": 300,
         "expected_return_pct": 8, "volatility_pct": 20}]})


# --------------------------------------------------------------------------- #
# The remainder IS the core
# --------------------------------------------------------------------------- #
def test_the_core_is_what_the_sleeves_have_not_claimed():
    sleeves = [_Fake("btm_trend_tqqq", 20.0), _Fake("btm_factor_stack", 15.0)]
    assert sv.total_pct(sleeves) == 35.0
    assert sv.remainder_pct(sleeves) == 65.0


def test_a_fully_allocated_book_has_no_core_rather_than_a_negative_one():
    """An over-allocated book is refused at write time, not rendered as a
    negative core -- a card cannot print "-10% core" and mean anything."""
    assert sv.remainder_pct([_Fake("a", 100.0)]) == 0.0
    assert sv.remainder_pct([_Fake("a", 80.0), _Fake("b", 40.0)]) == 0.0


def test_no_sleeves_means_the_whole_book_is_core():
    assert sv.remainder_pct([]) == 100.0
    assert sv.total_pct([]) == 0.0


# --------------------------------------------------------------------------- #
# The invariants C2..C4 will all lean on
# --------------------------------------------------------------------------- #
def test_over_allocating_abstains_with_a_reason_naming_the_room_left():
    """The same shape `_fund_sleeve` uses when it cannot fund: a refusal that
    only says "no" makes the user guess which number to change."""
    sleeves = [_Fake("btm_trend_tqqq", 70.0)]
    why = sv.validate(sleeves, strategy_id="btm_factor_stack", sleeve_pct=40.0)
    assert why is not None
    assert "30" in why, why
    assert sv.validate(sleeves, strategy_id="btm_factor_stack", sleeve_pct=30.0) is None


def test_resizing_a_sleeve_is_checked_against_the_others_not_against_itself():
    """Raising 20% to 25% must not be measured against a total that still
    counts the 20% being replaced."""
    sleeves = [_Fake("btm_trend_tqqq", 20.0), _Fake("btm_factor_stack", 75.0)]
    assert sv.validate(sleeves, strategy_id="btm_trend_tqqq", sleeve_pct=25.0) is None
    assert sv.validate(sleeves, strategy_id="btm_trend_tqqq", sleeve_pct=26.0) is not None


def test_the_same_strategy_cannot_be_added_twice():
    """Two rows for one strategy at two sizes is the ambiguity the single-column
    design was being replaced to remove -- reintroduced at N scale."""
    sleeves = [_Fake("btm_trend_tqqq", 20.0)]
    why = sv.validate(sleeves, strategy_id="btm_trend_tqqq", sleeve_pct=30.0,
                      replacing=False)
    assert why is not None and "already" in why


@pytest.mark.parametrize("pct", [0, -5, 101])
def test_a_sleeve_size_outside_the_book_is_refused(pct):
    assert sv.validate([], strategy_id="btm_trend_tqqq", sleeve_pct=pct) is not None


def test_a_sleeve_needs_a_strategy():
    assert sv.validate([], strategy_id="", sleeve_pct=20.0) is not None


# --------------------------------------------------------------------------- #
# The backfills are GONE (0019)
#
# `backfill_once` and `backfill_core_once` read the two dropped columns, so they
# could not survive them. Both had already been spent on every live database --
# production logged "0 sleeve(s) created" on every boot for weeks. What replaces
# the old tests here is the pair of properties that stop them coming back: the
# functions are absent, and the KV keys that recorded them running are NOT, so a
# future re-introduction cannot quietly re-run against a book that already
# migrated.
# --------------------------------------------------------------------------- #
def test_the_one_shot_backfills_no_longer_exist():
    assert not hasattr(sv, "backfill_once"), (
        "the sleeve backfill is back, and it reads columns that no longer exist")
    assert not hasattr(sv, "backfill_core_once"), "the core backfill is back"


def test_the_markers_that_recorded_them_running_are_kept():
    """Deleting the keys would make a re-introduced backfill run again.

    The keys are the record that the migration happened. They cost two rows and
    they are the only thing standing between a resurrected backfill and a book
    that has since had sleeves removed by hand.
    """
    assert sv.BACKFILL_KEY and sv.CORE_BACKFILL_KEY
    assert sv.BACKFILL_KEY != sv.CORE_BACKFILL_KEY


def test_apply_writes_the_row_itself_with_no_backfill_involved():
    """C1 shipped with a gap: the backfill was a one-shot, so a strategy applied
    after that boot had no sleeve row and showed up only under ``legacy``. C2
    closed it at the source -- apply writes the row. That is now the ONLY way a
    sleeve row is ever created, which is why the backfill could be deleted."""
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/strategies/btm_trend_tqqq/apply?sleeve_pct=15")
        body = c.get("/api/v1/plan/sleeves").json()
        assert [(r["strategy_id"], r["sleeve_pct"], r["is_core"])
                for r in body["sleeves"]] == [("btm_trend_tqqq", 15.0, False)]
        assert body["allocated_pct"] == 15.0
        assert body["core_pct"] == 85.0
        # And there is no second place to look.
        assert "legacy" not in body


def test_removing_a_sleeve_keeps_it_removed_across_a_restart():
    """The resurrect hazard, which the one-shot marker used to hold shut.

    With the backfill deleted there is nothing left to resurrect anything -- but
    the property is the point, not the mechanism, so it is asserted directly
    against a real second boot rather than against a marker value.
    """
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/strategies/btm_trend_tqqq/apply?sleeve_pct=15")
        c.delete("/api/v1/plan/sleeves/btm_trend_tqqq")
        assert c.get("/api/v1/plan/sleeves").json()["sleeves"] == []

    with TestClient(m.app) as c:                     # a whole new lifespan
        assert c.get("/api/v1/plan/sleeves").json()["sleeves"] == []
        assert _row_count() == 0


def test_a_static_family_is_not_invented_into_a_sleeve():
    """The static families are model portfolios, not sleeves. They govern the
    book as a CORE, and putting a made-up percentage on one would render a
    number nobody chose."""
    with TestClient(m.app) as c:
        _seed(c)
        static_id = _a_static_strategy_id(c)
        c.post(f"/api/v1/strategies/{static_id}/apply")
        body = c.get("/api/v1/plan/sleeves").json()
        assert body["sleeves"] == []
        assert (body["core"] or {}).get("strategy_id") == static_id


# --------------------------------------------------------------------------- #
# The endpoint reads, and only reads
# --------------------------------------------------------------------------- #
def test_the_endpoint_writes_nothing_not_even_a_self_healing_row():
    """A GET that quietly created state would make "does this book have a
    sleeve?" depend on whether anyone happened to open the page -- the same
    reason `peek_user` exists separately from `evaluate_user`.

    C2 made apply write rows, so "zero rows" is no longer the assertion. What
    still has to hold is that READING never changes the count, in either
    direction and from either starting point.
    """
    with TestClient(m.app) as c:
        _seed(c)
        assert _row_count() == 0
        for _ in range(3):
            c.get("/api/v1/plan/sleeves")
        assert _row_count() == 0, "a read invented a sleeve on an empty book"

        c.post("/api/v1/strategies/btm_trend_tqqq/apply?sleeve_pct=15")
        assert _row_count() == 1
        for _ in range(3):
            c.get("/api/v1/plan/sleeves")
        assert _row_count() == 1, "a read duplicated an existing sleeve"


def test_an_untouched_book_reports_a_whole_core_and_no_second_answer():
    with TestClient(m.app) as c:
        _seed(c)
        body = c.get("/api/v1/plan/sleeves").json()
        assert body["core_pct"] == 100.0
        assert body["core_is_implicit"] is True
        # Was `body["legacy"]["strategy"] is None`. A null that a reader can
        # still consult is exactly the failure 0019 removed, so the assertion is
        # that the block is ABSENT, not that it is empty.
        assert "legacy" not in body


# --------------------------------------------------------------------------- #
# One answer, and only one
# --------------------------------------------------------------------------- #
def test_applying_arms_the_cap_and_leaves_no_copy_on_the_plan_row():
    """Was `test_applying_still_writes_the_old_columns_and_still_arms_the_cap`.

    Writing the columns was the C1 rollback insurance, and it outlived its
    purpose by twelve days and 23 percentage points of drift. What still has to
    hold is the other half: applying arms the concentration cap.
    """
    with TestClient(m.app) as c:
        _seed(c)
        r = c.post("/api/v1/strategies/btm_trend_tqqq/apply?sleeve_pct=15").json()
        assert r["ok"] is True
        assert [x["ticker"] for x in r["sleeve_caps"]] == ["TQQQ"]
        plan = c.get("/api/v1/plan").json()
        assert "strategy" not in plan
        assert "strategy_sleeve_pct" not in plan


def test_the_plan_model_no_longer_carries_the_columns():
    """The payload could omit a field the table still has. The table is the
    thing 0019 is about, so assert the mapper, not the JSON."""
    from app.models.tables import Plan
    cols = set(Plan.__table__.columns.keys())
    assert "strategy" not in cols
    assert "strategy_sleeve_pct" not in cols


def test_is_core_is_reserved_and_never_written_by_apply():
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/strategies/btm_trend_tqqq/apply?sleeve_pct=15")
        assert all(s["is_core"] is False
                   for s in c.get("/api/v1/plan/sleeves").json()["sleeves"])


# --------------------------------------------------------------------------- #
# helpers -- own engine per call, in this test's own loop (Postgres rejects the
# app engine's connection across loops; see CLAUDE.md's sharp edges)
# --------------------------------------------------------------------------- #
def _in_session(fn):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings

    async def _run():
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as s:
                return await fn(s)
        finally:
            await eng.dispose()

    return asyncio.run(_run())


def _row_count():
    from sqlalchemy import func, select

    async def _q(s):
        return (await s.execute(select(func.count()).select_from(PlanSleeve))).scalar_one()
    return _in_session(_q)


def _a_static_strategy_id(c) -> str:
    from app.services import strategy_catalog as sc
    body = c.get("/api/v1/strategies").json()
    for goal in body["goals"]:
        if goal == sc.GOAL:
            continue
        cards = body["by_goal"].get(goal) or []
        if cards:
            return cards[0]["id"]
    raise AssertionError("no static strategy family in the catalog")
