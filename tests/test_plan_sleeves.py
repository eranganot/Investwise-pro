"""C1 -- a sleeve becomes a row, and nothing else moves.

``plans.strategy`` + ``plans.strategy_sleeve_pct`` can hold exactly one strategy.
Applying a second one overwrote the first, which is why the app looked like it
had two strategies when it had one. This phase adds the table that can hold N of
them, backfills the one that already exists, and deliberately stops there:
``apply_strategy`` still writes the old columns and the rest of the app still
reads them. The tests below therefore split in two -- what the new table does,
and the proof that the old path is untouched.

The core is the IMPLICIT REMAINDER in this model: sleeves sum to <= 100 and what
is left stays objective-managed. ``is_core`` exists but is never written.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app.models.tables import KVSetting, PlanSleeve
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
# Backfill
#
# Every test here has to model the real deploy order, and getting it wrong is
# how the first draft of this file failed. Booting the app IS a backfill: the
# lifespan calls `backfill_once` and spends the marker. So "a strategy that
# existed before the C1 deploy" means data first, marker unspent -- which is
# `_clear_marker()` after the apply, not a bare `_backfill()`.
# --------------------------------------------------------------------------- #
def test_a_strategy_applied_before_the_deploy_becomes_a_sleeve_row():
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/strategies/btm_trend_tqqq/apply?sleeve_pct=15")
        _clear_marker()                       # ...now the C1 deploy boots
        assert _backfill() == {"ran": True, "created": 1}
        body = c.get("/api/v1/plan/sleeves").json()
        assert body["sleeves"] == [{"strategy_id": "btm_trend_tqqq",
                                    "sleeve_pct": 15.0, "is_core": False}]
        assert body["allocated_pct"] == 15.0
        assert body["core_pct"] == 85.0


def test_booting_spends_the_marker_even_on_an_empty_book():
    """The lifespan runs it, so the first boot is the only boot that can.

    This is the mechanism the next test's window falls out of, and it is worth
    asserting directly rather than inferring from a failure somewhere else.
    """
    with TestClient(m.app) as c:
        _seed(c)
        assert _marker_value() == "0"
        assert _backfill()["ran"] is False


def test_a_strategy_applied_after_the_deploy_shows_only_under_legacy():
    """The C1 -> C2 window, stated rather than discovered.

    ``apply_strategy`` still writes only the ``plans`` columns in this phase, and
    the backfill is spent, so a strategy applied now has no sleeve row. The
    endpoint has to say so -- a reader seeing an empty ``sleeves`` list with no
    other signal would conclude the book has no strategy at all. C2 closes this
    by making apply write the row; the ``legacy`` block goes with it.
    """
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/strategies/btm_trend_tqqq/apply?sleeve_pct=15")
        body = c.get("/api/v1/plan/sleeves").json()
        assert body["sleeves"] == []
        assert body["legacy"] == {"strategy": "btm_trend_tqqq",
                                  "strategy_sleeve_pct": 15.0}


def test_the_backfill_never_runs_twice_even_after_a_sleeve_is_deleted():
    """The resurrect hazard, pinned now rather than discovered in C2.

    A per-boot "top up any subject with no rows" self-heal reads better right up
    until removing a sleeve exists -- then the next restart hands back the one
    the user just removed. The one-shot marker is what stops that.
    """
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/strategies/btm_trend_tqqq/apply?sleeve_pct=15")
        _clear_marker()
        assert _backfill() == {"ran": True, "created": 1}

        _delete_all_sleeves()
        again = _backfill()
        assert again["ran"] is False
        assert c.get("/api/v1/plan/sleeves").json()["sleeves"] == []


def test_a_book_with_no_strategy_backfills_nothing_but_still_spends_the_marker():
    with TestClient(m.app) as c:
        _seed(c)
        _clear_marker()
        assert _backfill() == {"ran": True, "created": 0}
        assert _marker_value() == "0"


def test_a_static_family_is_not_invented_into_a_sleeve():
    """The four static families are model portfolios, not sleeves. They govern
    the book through the objective, and putting a made-up percentage on one
    would render a number nobody chose."""
    with TestClient(m.app) as c:
        _seed(c)
        static_id = _a_static_strategy_id(c)
        c.post(f"/api/v1/strategies/{static_id}/apply")
        _clear_marker()
        assert _backfill()["created"] == 0
        assert c.get("/api/v1/plan/sleeves").json()["sleeves"] == []


def test_a_pre_0012_plan_with_no_size_falls_back_to_the_suggestion_not_to_100():
    """A plan carrying a strategy but no sleeve_pct predates 0012. Backfilling
    it at 100% would put a whole book into a 3x fund on a deploy -- the same
    refusal `sleeve_basket` makes for the same reason."""
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/strategies/btm_trend_tqqq/apply?sleeve_pct=15")
        _null_the_sleeve_pct()
        _clear_marker()
        assert _backfill()["created"] == 1
        from app.services import strategy_catalog as sc
        suggested = float(sc.get("btm_trend_tqqq")["sleeve_pct"])
        got = c.get("/api/v1/plan/sleeves").json()["sleeves"][0]["sleeve_pct"]
        assert got == suggested < 100.0


# --------------------------------------------------------------------------- #
# The endpoint reads, and only reads
# --------------------------------------------------------------------------- #
def test_the_endpoint_writes_nothing_not_even_a_self_healing_row():
    """A GET that quietly created state would make "does this book have a
    sleeve?" depend on whether anyone happened to open the page -- the same
    reason `peek_user` exists separately from `evaluate_user`."""
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/strategies/btm_trend_tqqq/apply?sleeve_pct=15")
        for _ in range(3):
            c.get("/api/v1/plan/sleeves")
        assert _row_count() == 0


def test_an_untouched_book_reports_a_whole_core_and_no_legacy_strategy():
    with TestClient(m.app) as c:
        _seed(c)
        body = c.get("/api/v1/plan/sleeves").json()
        assert body["core_pct"] == 100.0
        assert body["core_is_implicit"] is True
        assert body["legacy"]["strategy"] is None


# --------------------------------------------------------------------------- #
# C1 is inert
# --------------------------------------------------------------------------- #
def test_applying_still_writes_the_old_columns_and_still_arms_the_cap():
    """The whole claim of this phase: the table lands and sits. If apply had
    started routing through it, this is what would have quietly changed."""
    with TestClient(m.app) as c:
        _seed(c)
        r = c.post("/api/v1/strategies/btm_trend_tqqq/apply?sleeve_pct=15").json()
        assert r["ok"] is True
        assert [x["ticker"] for x in r["sleeve_caps"]] == ["TQQQ"]
        plan = c.get("/api/v1/plan").json()
        assert plan["strategy"] == "btm_trend_tqqq"
        assert plan["strategy_sleeve_pct"] == 15.0


def test_the_backfill_does_not_disturb_the_columns_it_reads():
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/strategies/btm_trend_tqqq/apply?sleeve_pct=15")
        _clear_marker()
        _backfill()
        plan = c.get("/api/v1/plan").json()
        assert plan["strategy"] == "btm_trend_tqqq"
        assert plan["strategy_sleeve_pct"] == 15.0


def test_is_core_is_reserved_and_never_written():
    with TestClient(m.app) as c:
        _seed(c)
        c.post("/api/v1/strategies/btm_trend_tqqq/apply?sleeve_pct=15")
        _clear_marker()
        _backfill()
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


def _backfill():
    return _in_session(sv.backfill_once)


def _row_count():
    from sqlalchemy import func, select

    async def _q(s):
        return (await s.execute(select(func.count()).select_from(PlanSleeve))).scalar_one()
    return _in_session(_q)


def _delete_all_sleeves():
    from sqlalchemy import delete

    async def _q(s):
        await s.execute(delete(PlanSleeve))
        await s.commit()
    return _in_session(_q)


def _marker_value():
    async def _q(s):
        row = await s.get(KVSetting, sv.BACKFILL_KEY)
        return row.value if row else None
    return _in_session(_q)


def _clear_marker():
    """Put the deploy back before its first boot, with the data already there.

    Booting the TestClient runs the lifespan, which runs the backfill -- so
    without this every backfill test would be asserting against a marker that
    was already spent on an empty database.
    """
    from sqlalchemy import delete

    async def _q(s):
        await s.execute(delete(KVSetting).where(KVSetting.key == sv.BACKFILL_KEY))
        await s.commit()
    return _in_session(_q)


def _null_the_sleeve_pct():
    """Make the plan look like it predates migration 0012."""
    from sqlalchemy import update

    from app.models.tables import Plan

    async def _q(s):
        await s.execute(update(Plan).values(strategy_sleeve_pct=None))
        await s.commit()
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
