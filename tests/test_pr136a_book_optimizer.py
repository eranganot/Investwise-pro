"""PR 136a (wave 5): the best book the app can measure for the goal, goal unchanged.

Owner, 2026-10-15/18: optimise the book for the goal without touching the goal; a new
user with an empty book gets a starter book; "it's important to make this [the core]
a variable and not hard coded".

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services import book_optimizer as bo  # noqa: E402
from app.services import strategy_catalog as cat  # noqa: E402
from tests.test_frontier_goal import _reset_plan, _seed  # noqa: E402

VOL = "btm_vol_target_tqqq"


# --------------------------------------------------------------------------- #
# candidates (pure)
# --------------------------------------------------------------------------- #
def test_candidates_never_add_a_look_alike_or_an_explore_only_and_remove_each():
    cs = bo.candidates({VOL: 40.0}, {})
    adds = {c["sleeve"] for c in cs if c["kind"] == "add"}
    assert "btm_trend_tqqq" not in adds and "div_qqq_trend_short" not in adds
    assert "r2_quality_momentum_5" not in adds
    assert "div_dbmf" in adds
    assert [c["sleeve"] for c in cs if c["kind"] == "remove"] == [VOL]


def test_an_empty_book_only_adds():
    cs = bo.candidates({}, {})
    assert cs and all(c["kind"] == "add" for c in cs)
    assert {c["sleeve"] for c in cs} == {i for i in cat.active_ids() if not cat.get(i).get("explore_only")}


def test_no_room_no_add():
    assert not [c for c in bo.candidates({VOL: 99.5}, {}) if c["kind"] == "add"]


# --------------------------------------------------------------------------- #
# the search rules, on a scored stub
# --------------------------------------------------------------------------- #
def _fake_scores(gains: dict, halves: dict | None = None, base=10.0, base_halve=5.0):
    """score_books stand-in: a book's chance = base + the gain of each sleeve it holds."""
    halves = halves or {}

    async def fake(session, user, books, *, extra_sleeves=None, nominal_nav=None, full=False):
        out = []
        for b in books:
            p = base + sum(gains.get(s, 0.0) for s in b)
            h = base_halve + sum(halves.get(s, 0.0) for s in b)
            out.append({"ok": True, "p_goal_pct": p, "p_halve_pct": h, "median": 1.0, "growth_cagr_pct": 1.0,
                        "proposed": {}})
        return {"ok": True, "scores": out}
    return fake


async def _book(own_session, sleeves):
    from app.services import sleeve_service as sv
    from app.services.feed_service import ensure_superadmin
    from app.services.plan_service import upsert_plan
    async with own_session() as s:
        u = await ensure_superadmin(s)
        await upsert_plan(s, u, objective="Grow", target_roi_pct=30.0, target_date="2031")
        for sid, pct in sleeves.items():
            await sv.add_or_resize(s, u, sid, pct)
        await s.commit()


async def test_steps_are_kept_best_first_only_above_the_minimum(own_session, monkeypatch):
    from app.services import frontier_service as fs
    from app.services.feed_service import ensure_superadmin
    monkeypatch.setattr(fs, "score_books", _fake_scores({"div_dbmf": 3.0, "div_gold_trend": 1.0,
                                                         "div_kmlm": 0.2}))
    await _book(own_session, {VOL: 40.0})
    async with own_session() as s:
        out = await bo.optimize(s, await ensure_superadmin(s))
    assert out["ok"], out
    assert [x["sleeve"] for x in out["steps"]][:2] == ["div_dbmf", "div_gold_trend"]
    assert "div_kmlm" not in [x["sleeve"] for x in out["steps"]], "0.2 points is under the 0.5 minimum"


async def test_a_step_that_breaks_the_halving_limit_is_not_taken(own_session, monkeypatch):
    from app.services import frontier_service as fs
    from app.services.feed_service import ensure_superadmin
    monkeypatch.setattr(fs, "score_books", _fake_scores({"div_2x_gold_trend": 9.0, "div_dbmf": 1.0},
                                                        halves={"div_2x_gold_trend": 90.0}))
    await _book(own_session, {VOL: 40.0})
    async with own_session() as s:
        out = await bo.optimize(s, await ensure_superadmin(s))
    picked = [x["sleeve"] for x in out["steps"]]
    assert "div_2x_gold_trend" not in picked and picked[0] == "div_dbmf"


async def test_the_minimum_step_is_a_setting(own_session, monkeypatch):
    from app.services import frontier_service as fs
    from app.services import thresholds
    from app.services.feed_service import ensure_superadmin
    assert thresholds.defaults()["optimizer_min_step_pts"] == 0.5
    monkeypatch.setattr(fs, "score_books", _fake_scores({"div_dbmf": 1.0}))
    await _book(own_session, {VOL: 40.0})
    tok = thresholds._OVERRIDES.set({"optimizer_min_step_pts": 2.0})
    try:
        async with own_session() as s:
            out = await bo.optimize(s, await ensure_superadmin(s))
    finally:
        thresholds._OVERRIDES.reset(tok)
    assert out["steps"] == [] and out["min_step_pts"] == 2.0


# --------------------------------------------------------------------------- #
# the real model
# --------------------------------------------------------------------------- #
async def test_a_real_run_on_a_book_with_a_goal_is_stored_and_served(own_session):
    try:
        with TestClient(m.app) as c:
            _seed(c, 30)
            c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20000, "mode": "set"})
            assert c.post(f"/api/v1/plan/sleeves/btm_qqq_hold/swap?to={VOL}").json().get("ok")
            assert c.get("/api/v1/plan/optimizer").json()["reason"] == "NOT_RUN"
            r = c.post("/api/v1/plan/optimizer/refresh").json()
            assert r.get("ok") and r.get("stored"), r
            assert r["starter"] is False and r["before"]["p_goal_pct"] is not None
            assert r["candidates_tried"] > 0
            if r["steps"]:
                assert r["after"]["p_goal_pct"] >= r["before"]["p_goal_pct"] - 1e-6
            assert c.get("/api/v1/plan/optimizer").json()["as_of"] == r["as_of"]
    finally:
        await _reset_plan(own_session)


async def test_an_empty_book_gets_a_starter_book(own_session):
    try:
        with TestClient(m.app) as c:
            c.put("/api/v1/plan", json={"objective": "Grow", "target_roi_pct": 30, "target_date": "2031"})
            r = c.post("/api/v1/plan/optimizer/refresh").json()
        assert r.get("ok"), r
        assert r["starter"] is True and r["nominal_nav"] == bo.NOMINAL_NAV and r["held"] == {}
        assert r["before"]["p_goal_pct"] is not None, "the all-core book is scored"
        assert all(st["kind"] == "add" for st in r["steps"])
    finally:
        await _reset_plan(own_session)


async def test_no_goal_no_optimiser(own_session):
    from app.services.feed_service import ensure_superadmin
    async with own_session() as s:
        out = await bo.optimize(s, await ensure_superadmin(s))
    assert out["ok"] is False and out["reason"] == "NO_GOAL"


async def test_the_core_is_read_at_run_time_never_fixed(own_session, monkeypatch):
    """Owner: the core mix must be a variable. Change the core, and the optimiser's
    solves use the new basket."""
    from app.services import frontier_service as fs
    from app.services import sleeve_service as sv
    from app.services import strategies as static_cat
    from app.services.feed_service import ensure_superadmin
    seen = []
    real = fs.goal_solve

    def spy(prep, **kw):
        seen.append(dict(kw["core_basket"]))
        return real(prep, **kw)

    monkeypatch.setattr(fs, "goal_solve", spy)
    cores = [c["id"] for c in static_cat.core_candidates()][:2]
    try:
        with TestClient(m.app) as c:
            c.put("/api/v1/plan", json={"objective": "Grow", "target_roi_pct": 30, "target_date": "2031"})
        baskets = []
        for core in cores:
            async with own_session() as s:
                u = await ensure_superadmin(s)
                r = await sv.set_core(s, u, core)
                assert r.get("ok"), r
                await s.commit()
            seen.clear()
            async with own_session() as s:
                await bo.optimize(s, await ensure_superadmin(s))
            assert seen, "no solve ran"
            baskets.append(seen[0])
        assert baskets[0] == static_cat.core_weights(cores[0])
        assert baskets[1] == static_cat.core_weights(cores[1])
        assert baskets[0] != baskets[1]
    finally:
        await _reset_plan(own_session)


@pytest.mark.parametrize("x", [None])
def test_the_job_is_registered(x):
    import app.worker.scheduler as sch
    assert "book_optimizer" in open(sch.__file__, encoding="utf-8").read()
