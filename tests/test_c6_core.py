"""C6 -- the core stops being an anonymous remainder.

Two things were wrong, both found on the live screen rather than by reading code.

**The core had no identity.** It was rendered as a bare percentage with a
sentence saying an objective managed it. The 14 static families ARE core
choices -- whole-book target mixes -- but applying one left no durable trace, so
nothing could be ticked and the panel had nothing to name.

**And the target mix flickered.** ``recommendations`` read the core's target
allocation out of ``plans.strategy``, which holds the most recently applied
strategy *of either kind*. Apply a static family and the book's target mix
became that family's; resize any sleeve and it silently reverted to the
objective's. That one is the important test in this file
(``test_resizing_a_sleeve_does_not_change_the_cores_target_mix``) -- it is the
defect, and it is invisible from the screen.

The design constraint C1 set still holds: **the core is a remainder.** The
``is_core`` row records WHICH strategy manages the core, never HOW BIG it is --
``sleeve_pct`` on it is always 0 and the size stays computed. Half these tests
exist to prove the row cannot leak into anything that measures sleeves.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app.services import sleeve_service as sv

CORE = "bal_6040"            # 60/40 -- differs from every OBJ_TARGET mix
CORE2 = "grow_quality"       # 100% Equities
SOXL = "btm_trend_soxl"
FACTOR = "btm_factor_stack"


def _pos(ticker, price=100.0, qty=100.0, basis=90.0):
    return {"ticker": ticker, "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": basis, "expected_return_pct": 8, "volatility_pct": 20}


def _seed(c, extra=()):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("V"), _pos("SCHD"), _pos("MSFT"), *[_pos(t) for t in extra]]})


def _apply(c, sid, pct=None):
    q = f"?sleeve_pct={pct}" if pct is not None else ""
    return c.post(f"/api/v1/strategies/{sid}/apply{q}").json()


def _session():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import app.models  # noqa: F401
    from app.core.config import get_settings
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


async def _fresh():
    from app.models.base import Base
    from app.services.feed_service import ensure_superadmin
    eng, Session = _session()
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    s = Session()
    return eng, s, await ensure_superadmin(s)


# --------------------------------------------------------------------------- #
# The choke point: a core row is not a sleeve
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_core_row_never_reaches_anything_that_measures_sleeves():
    """`list_sleeves` filtering is_core out is the single thing keeping C6 from
    changing sleeve behaviour. If it regresses, the core would claim a share of
    the book in `total_pct` and arm max_weight caps on its whole basket."""
    eng, s, user = await _fresh()
    try:
        await sv.add_or_resize(s, user, SOXL, 10)
        await sv.set_core(s, user, CORE)
        await s.commit()

        sleeves = await sv.list_sleeves(s, user)
        assert [x.strategy_id for x in sleeves] == [SOXL], "the core leaked into list_sleeves"
        assert sv.total_pct(sleeves) == pytest.approx(10.0)
        assert sv.remainder_pct(sleeves) == pytest.approx(90.0)

        targets = await sv.all_sleeve_targets(s, user)
        assert "BND" not in targets and "VTI" not in targets, (
            "the core's basket became sleeve targets -- caps would be armed on it")
    finally:
        await s.close()
        await eng.dispose()


@pytest.mark.asyncio
async def test_the_core_row_carries_no_size_of_its_own():
    """C1 decided the core is a remainder. C6 gives it a name, not a percentage:
    two stored answers to 'how big is the core' would eventually disagree."""
    eng, s, user = await _fresh()
    try:
        await sv.set_core(s, user, CORE)
        await s.commit()
        row = await sv.get_core(s, user)
        assert row is not None and float(row.sleeve_pct) == 0.0
        assert row.is_core is True
    finally:
        await s.close()
        await eng.dispose()


# --------------------------------------------------------------------------- #
# Setting, replacing, refusing, clearing
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_choosing_a_second_core_replaces_the_first():
    """Singular by construction. Two is_core rows would give `get_core` an
    oldest-first answer that quietly ignored the newer choice."""
    eng, s, user = await _fresh()
    try:
        assert (await sv.set_core(s, user, CORE))["action"] == "set"
        out = await sv.set_core(s, user, CORE2)
        assert out["action"] == "changed" and out["previous"] == CORE
        await s.commit()
        assert await sv.core_strategy_id(s, user) == CORE2
        rows = (await s.scalars(__import__("sqlalchemy").select(
            __import__("app.models.tables", fromlist=["PlanSleeve"]).PlanSleeve))).all()
        assert len([r for r in rows if r.is_core]) == 1
    finally:
        await s.close()
        await eng.dispose()


@pytest.mark.asyncio
async def test_a_rule_based_sleeve_is_refused_as_a_core():
    """A sleeve is a mechanical rule over a share of NAV; a core is a whole-book
    mix. Accepting `btm_trend_soxl` here would hand the book a target allocation
    derived from a 3x fund's model basket."""
    eng, s, user = await _fresh()
    try:
        out = await sv.set_core(s, user, SOXL)
        assert out["ok"] is False
        assert "sleeve" in out["reason"] and "Add it as a sleeve" in out["reason"]
        assert await sv.get_core(s, user) is None, "a refused core still wrote a row"
    finally:
        await s.close()
        await eng.dispose()


@pytest.mark.asyncio
async def test_clearing_the_core_leaves_the_sleeves_alone():
    eng, s, user = await _fresh()
    try:
        await sv.add_or_resize(s, user, SOXL, 10)
        await sv.set_core(s, user, CORE)
        await s.commit()
        assert (await sv.clear_core(s, user))["removed"] == CORE
        await s.commit()
        assert await sv.get_core(s, user) is None
        assert [x.strategy_id for x in await sv.list_sleeves(s, user)] == [SOXL]
        assert (await sv.clear_core(s, user))["ok"] is False, "clearing twice must not pretend"
    finally:
        await s.close()
        await eng.dispose()


# --------------------------------------------------------------------------- #
# Apply routes the two kinds to the two places
# --------------------------------------------------------------------------- #
def test_applying_a_static_family_sets_the_core_and_no_sleeve():
    with TestClient(m.app) as c:
        _seed(c)
        r = _apply(c, CORE)
        assert r["ok"] and r["core"]["ok"] and r["core"]["strategy_id"] == CORE
        assert r["sleeve"] is None
        assert r["core_note"] and "Nothing was bought or sold" in r["core_note"]
        s = c.get("/api/v1/plan/sleeves").json()
        assert s["core"]["strategy_id"] == CORE
        assert s["core"]["target_allocation"] == {"Equities": 0.6, "Fixed Income": 0.4}
        assert s["sleeves"] == []


def test_applying_a_sleeve_does_not_touch_the_core():
    with TestClient(m.app) as c:
        _seed(c)
        _apply(c, CORE)
        r = _apply(c, SOXL, 10)
        assert r["ok"] and r["core"] is None and r["sleeve"]["ok"]
        s = c.get("/api/v1/plan/sleeves").json()
        assert s["core"]["strategy_id"] == CORE, "adding a sleeve replaced the core"
        assert [x["strategy_id"] for x in s["sleeves"]] == [SOXL]


# --------------------------------------------------------------------------- #
# The defect: the target mix used to depend on the last card you pressed
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_core_target_is_the_familys_mix_and_falls_back_to_the_objective():
    from app.services.allocation_mix import OBJ_TARGET
    from app.services.recommendations import _core_target

    eng, s, user = await _fresh()
    try:
        assert await _core_target(s, user, "Grow") == OBJ_TARGET["Grow"]
        await sv.set_core(s, user, CORE)
        await s.commit()
        assert await _core_target(s, user, "Grow") == {"Equities": 0.6, "Fixed Income": 0.4}
        await sv.clear_core(s, user)
        await s.commit()
        assert await _core_target(s, user, "Grow") == OBJ_TARGET["Grow"]
    finally:
        await s.close()
        await eng.dispose()


@pytest.mark.asyncio
async def test_resizing_a_sleeve_does_not_change_the_cores_target_mix():
    """THE defect C6 exists to fix.

    Both readers took the target from ``plans.strategy``, which apply overwrites
    with whatever was applied last -- of either kind. So:

        apply bal_6040        -> target 60/40
        resize any sleeve     -> plans.strategy now names the sleeve,
                                 _strat.get() misses, target silently reverts
                                 to OBJ_TARGET[objective]

    Your book's target asset mix depended on which card you pressed last, and
    nothing on any screen said so.
    """
    from app.services.recommendations import _core_target
    from app.services.strategy_service import apply_strategy

    eng, s, user = await _fresh()
    try:
        await apply_strategy(s, user, CORE)
        before = await _core_target(s, user, "Balanced")
        assert before == {"Equities": 0.6, "Fixed Income": 0.4}

        await apply_strategy(s, user, SOXL, sleeve_pct=10)
        await apply_strategy(s, user, SOXL, sleeve_pct=15)   # a resize, the common case
        after = await _core_target(s, user, "Balanced")

        assert after == before, (
            f"the core's target mix moved to {after} because a SLEEVE was resized")
        # And the legacy column really did move under it -- proving the test is
        # exercising the thing it claims to, not passing because nothing changed.
        from app.services.plan_service import get_plan
        assert (await get_plan(s, user)).strategy == SOXL
    finally:
        await s.close()
        await eng.dispose()


# --------------------------------------------------------------------------- #
# Replacing the book cannot silently sell your sleeves
# --------------------------------------------------------------------------- #
def test_replace_book_is_refused_when_the_book_runs_sleeves():
    """`_replace_book` deletes every holding and inserts the basket. It is
    entirely sleeve-unaware, so on this book it would sell SOXL and leave the
    plan_sleeves row pointing at nothing. Phase C shipped five sub-phases past
    this button without coming back to it."""
    with TestClient(m.app) as c:
        _seed(c, extra=("SOXL",))
        _apply(c, SOXL, 10)
        r = c.post(f"/api/v1/strategies/{CORE}/load-basket",
                   json={"mode": "replace", "dry_run": True}).json()
        assert r["ok"] is False
        assert "sell your sleeves" in r["error"]
        assert SOXL in r["sleeves"]
        # The refusal has to point somewhere, or it is a dead end.
        assert "core" in r["reason"]


def test_replace_book_still_works_on_a_book_with_no_sleeves():
    """The refusal is scoped to the hazard, not to the feature."""
    with TestClient(m.app) as c:
        _seed(c)
        r = c.post(f"/api/v1/strategies/{CORE}/load-basket",
                   json={"mode": "replace", "dry_run": True}).json()
        assert r["ok"] is True and r["mode"] == "replace"
        assert {x["ticker"] for x in r["removing"]} == {"V", "SCHD", "MSFT"}


# --------------------------------------------------------------------------- #
# Backfill
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_backfill_adopts_a_static_family_but_never_a_sleeve():
    from app.models.tables import Plan

    eng, s, user = await _fresh()
    try:
        s.add(Plan(user_id=user.id, strategy=CORE))
        await s.commit()
        out = await sv.backfill_core_once(s)
        assert out["ran"] and out["created"] == 1
        assert await sv.core_strategy_id(s, user) == CORE
        assert (await sv.backfill_core_once(s))["ran"] is False, "must be a one-shot"
    finally:
        await s.close()
        await eng.dispose()


@pytest.mark.asyncio
async def test_backfill_does_not_resurrect_a_core_you_cleared():
    """The reason this is a one-shot with its own KV key rather than a
    read-through fallback on ``plans.strategy``: the fallback version hands the
    choice straight back on the next page load after you clear it."""
    from app.models.tables import Plan

    eng, s, user = await _fresh()
    try:
        s.add(Plan(user_id=user.id, strategy=CORE))
        await s.commit()
        await sv.backfill_core_once(s)
        await sv.clear_core(s, user)
        await s.commit()
        await sv.backfill_core_once(s)          # a later boot
        assert await sv.get_core(s, user) is None, "the backfill resurrected a cleared core"
    finally:
        await s.close()
        await eng.dispose()


@pytest.mark.asyncio
async def test_backfill_skips_a_plan_whose_strategy_is_a_sleeve():
    """A rule-based id in ``plans.strategy`` says the last thing applied was a
    sleeve. It says nothing about the core, and guessing is the ambiguity C6
    exists to remove."""
    from app.models.tables import Plan

    eng, s, user = await _fresh()
    try:
        s.add(Plan(user_id=user.id, strategy=FACTOR, strategy_sleeve_pct=8.0))
        await s.commit()
        out = await sv.backfill_core_once(s)
        assert out["created"] == 0
        assert await sv.get_core(s, user) is None
    finally:
        await s.close()
        await eng.dispose()
