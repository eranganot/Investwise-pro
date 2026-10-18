"""PR 135b (wave 4): the sleeve cards -- move into a hedge, remove, replace a look-alike.

Each card shows what its move does to the goal's chance with your rules and to the
chance the book halves (owner decision 4: shown both ways -- "raises" and "safer,
lower chance"). Accept sets the sleeve sizes; it sells nothing, and says so.

The card-claims rule (CLAUDE.md): a card may not claim a change it does not produce,
so the last test applies a scored move and re-measures -- the sizes and the goal
chance the card quotes must reappear.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import json  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services import sleeve_moves as sm  # noqa: E402
from tests.test_frontier_goal import _reset_plan, _seed  # noqa: E402

VOL = "btm_vol_target_tqqq"
SEMIS = "btm_trend_soxl"


def _mv(kind, goal, halve, frm=VOL, to="div_dbmf", scale=1, keep=None):
    return {"kind": kind, "from": frm, "to": to, "scale": scale, "keep": keep, "pct": 15, "ok": True,
            "book": {VOL: 25.0, to: 15.0} if to else {SEMIS: 26.5},
            "after": {"p_goal_pct": 20 + goal, "p_halve_pct": 60 + (halve or 0)},
            "goal_pts": goal, "halve_pts": halve}


def _stored(moves):
    return {"ok": True, "as_of": "2026-10-18T04:40:00+00:00", "held": {VOL: 40.0},
            "before": {"p_goal_pct": 20.0, "p_halve_pct": 60.0}, "moves": moves}


# --------------------------------------------------------------------------- #
# which moves become cards (pure)
# --------------------------------------------------------------------------- #
def test_raises_are_capped_and_best_first():
    st = _stored([_mv("hedge", 6, -1, to="a"), _mv("hedge", 9, 2, to="b"), _mv("hedge", 7, -3, to="c"),
                  _mv("hedge", 2, -1, to="d")])
    sel = [x for x in sm.select(st, 5.0) if x["verdict"] == "raises"]
    assert [x["to"] for x in sel] == ["b", "c"]


def test_safer_lower_chance_is_shown_once_and_only_when_material():
    st = _stored([_mv("hedge", -1.5, -12, to="a"), _mv("hedge", -0.5, -6, to="b"), _mv("hedge", -1, -3, to="c")])
    sel = sm.select(st, 5.0)
    assert [(x["to"], x["verdict"]) for x in sel] == [("a", "safer")]


def test_nothing_material_means_no_card():
    assert sm.select(_stored([_mv("hedge", 1, -1), _mv("remove", -2, -4, to=None)]), 5.0) == []


def test_the_best_replacement_for_a_look_alike_always_shows():
    st = _stored([_mv("replace", -3, 1, to="x", keep=SEMIS), _mv("replace", 1, 0, to="y", keep=SEMIS)])
    sel = sm.select(st, 5.0)
    assert [(x["to"], x["verdict"]) for x in sel] == [("y", "same_bet")]


# --------------------------------------------------------------------------- #
# the cards on a book
# --------------------------------------------------------------------------- #
async def _store(own_session, stored):
    from app.models.tables import KVSetting
    from app.services.feed_service import ensure_superadmin
    async with own_session() as s:
        u = await ensure_superadmin(s)
        s.add(KVSetting(key=sm.KV_PREFIX + u.email, value=json.dumps(stored)))
        await s.commit()


async def test_a_card_says_both_numbers_and_that_accept_sells_nothing(own_session):
    with TestClient(m.app) as c:
        _seed(c, 30)
        assert c.post(f"/api/v1/plan/sleeves/btm_qqq_hold/swap?to={VOL}").json().get("ok")
        c.post(f"/api/v1/strategies/{VOL}/apply?sleeve_pct=40")
        await _store(own_session, _stored([_mv("hedge", 6, -10), _mv("hedge", -2, -11, to="div_kmlm")]))
        recs = c.get("/api/v1/recommendations").json()
        cards = [r for r in recs["recommendations"] + recs.get("more_recommendations", [])
                 if (r.get("apply") or {}).get("kind") == "sleeve_move"]
    titles = {r["title"] for r in cards}
    assert "Move 15% of Volatility-Targeted Leverage into Managed Futures (DBMF)" in titles
    assert any(t.startswith("Safer, lower chance: ") for t in titles)
    for r in cards:
        assert "goal chance 20% ->" in r["why"] and "chance the book halves 60% ->" in r["why"]
        assert "Nothing is bought or sold" in r["impact"]
        assert r["apply"]["book"] and r["apply"]["held"] == {VOL: 40.0}


async def test_no_card_when_the_scores_are_about_a_different_book(own_session):
    with TestClient(m.app) as c:
        _seed(c, 30)
        assert c.post(f"/api/v1/plan/sleeves/btm_qqq_hold/swap?to={VOL}").json().get("ok")
        c.post(f"/api/v1/strategies/{VOL}/apply?sleeve_pct=35")        # not the 40% that was scored
        await _store(own_session, _stored([_mv("hedge", 6, -10)]))
        recs = c.get("/api/v1/recommendations").json()
    assert not [r for r in recs["recommendations"] + recs.get("more_recommendations", [])
                if (r.get("apply") or {}).get("kind") == "sleeve_move"]


async def test_accept_refuses_untouched_when_the_book_moved(own_session):
    from app.services.feed_service import ensure_superadmin
    with TestClient(m.app) as c:
        _seed(c, 30)
    async with own_session() as s:
        out = await sm.apply_move(s, await ensure_superadmin(s),
                                  {"held": {VOL: 40.0}, "book": {VOL: 25.0, "div_dbmf": 15.0}})
    assert out["ok"] is False and out["refused"] == "BOOK_CHANGED"


# --------------------------------------------------------------------------- #
# card claims: apply, re-measure
# --------------------------------------------------------------------------- #
async def test_applying_a_scored_move_produces_the_sizes_and_the_chance_it_claimed(own_session):
    from app.services import frontier_service as fs
    from app.services import sleeve_service as sv
    from app.services.feed_service import ensure_superadmin
    try:
        with TestClient(m.app) as c:
            _seed(c, 30)
            c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20000, "mode": "set"})
            assert c.post(f"/api/v1/plan/sleeves/btm_qqq_hold/swap?to={VOL}").json().get("ok")
            r = c.post("/api/v1/plan/sleeve-moves/refresh").json()
            assert r.get("stored"), r
        move = next(x for x in r["moves"] if x["ok"] and x["kind"] == "hedge")
        async with own_session() as s:
            u = await ensure_superadmin(s)
            out = await sm.apply_move(s, u, {"held": r["held"], "book": move["book"]})
            assert out["ok"], out
            now = {x.strategy_id: float(x.sleeve_pct) for x in await sv.list_sleeves(s, u)}
            assert now == pytest.approx({k: float(v) for k, v in move["book"].items() if v > 0})
            again = await fs.score_books(s, u, [now])
        assert again["ok"] and again["scores"][0]["p_goal_pct"] == pytest.approx(move["after"]["p_goal_pct"])
    finally:
        await _reset_plan(own_session)
