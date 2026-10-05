"""PR 96 - protection tuned for the goal, under a halving floor; Accept retunes the rules."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import frontier_service as fs

from tests.test_frontier_goal import _cards, _goal_verdict, _reset_plan, _seed
from tests.test_frontier_resize import EST_KW, _prep, _store_verdict


def _stats(p, h, med=40000, w5=20000):
    return {"p_goal_pct": p, "p_halve_pct": h, "median": med, "worst5_mean": w5}


def test_the_best_goal_odds_under_the_halving_floor_wins():
    table = {(False, 10.0): _stats(0.5, 0.0), (False, 15.0): _stats(0.6, 0.0), (False, 20.0): _stats(0.7, 0.1),
             (True, 10.0): _stats(19.9, 0.6), (True, 15.0): _stats(22.7, 0.8), (True, 20.0): _stats(24.7, 2.9)}
    sl = [{"col": 0, "trail": 0.2}]
    guard = {"level": 0.10, "rearm": 0.075, "fraction": 0.25, "col": 0}

    def run(sl=None, gd="same", **kw):
        retire = sl is not None and sl[0].get("trail") is None
        lvl = round(gd["level"] * 100, 2) if gd and gd != "same" else 10.0
        return table[(retire, lvl)]
    rules = {"sleeves": {"soxl": {"exit_rule_ids": ["r1", "r2"], "rules": ["trailing_stop SOXL (20%)",
                                                                       "take_profit SOXL (+13%)"]}},
             "guard": {"rule_id": "g1"}}
    t = fs._tune_protection(run, sl, guard, rules, ["soxl"], np.zeros((10, 1)), 1, table[(False, 10.0)])
    assert t["material"] and t["best"]["retire_sleeve_exits"] and t["best"]["guard_pct"] == 15.0  # 20% halves 2.9% > the 1% limit (PR 97)
    assert t["changes"]["pause_rule_ids"] == ["r1", "r2"]
    assert t["changes"]["pause_rules"] == ["trailing_stop SOXL (20%)"]          # take-profits stay
    assert t["changes"]["guard"] == {"rule_id": "g1", "from_pct": 10.0, "to_pct": 15.0}
    # a setup that halves too often never wins, however good its odds
    table[(True, 20.0)] = _stats(40.0, 9.0)
    t2 = fs._tune_protection(run, sl, guard, rules, ["soxl"], np.zeros((10, 1)), 1, table[(False, 10.0)])
    assert t2["best"]["guard_pct"] == 15.0


def test_no_change_when_nothing_beats_today_materially():
    def run(sl=None, gd="same", **kw):
        return _stats(10.0, 1.0)
    t = fs._tune_protection(run, [{"col": 0, "trail": 0.2}], {"level": 0.1, "rearm": 0.075, "fraction": 0.25,
                                                               "col": 0},
                            {"sleeves": {}, "guard": {}}, ["s"], np.zeros((10, 1)), 1, _stats(9.0, 1.0))
    assert t["material"] is False and t["changes"] is None


def test_goal_solve_carries_a_tuning_that_respects_the_floor():
    rules = {"sleeves": {"lev": {"trail": 0.2, "tp_ratio": 1.13, "tp_fraction": 0.25, "exit_rule_ids": ["x"],
                                 "rules": ["trailing_stop LEV (20%)"]}},
             "guard": {"level": 0.10, "rearm": 0.075, "fraction": 0.25, "rule_id": "g"}, "not_simulated": []}
    v = fs.goal_solve(_prep(), sleeves_pct={"lev": 40.0, "calm": 3.0}, core_basket={"VTI": 0.6, "BND": 0.4},
                      cash_pct=4.0, objective="Grow", est_kw=EST_KW, nav=10_000, target=30_000, years=5.0,
                      draws=0, rules=rules)
    tu = v["rules"]["tuning"]
    assert tu and len(tu["candidates"]) == 6
    if tu["best"]:
        assert tu["best"]["stats"]["p_halve_pct"] <= fs.HALVE_FLOOR_PCT


async def _make_rules(own_session):
    from app.services.feed_service import ensure_superadmin
    from app.services.rules_service import create_rule
    async with own_session() as s:
        user = await ensure_superadmin(s)
        a = await create_rule(s, user, ticker="QQQ", rule_type="trailing_stop", mode="pct", level=10.0)
        g = await create_rule(s, user, ticker="__BOOK__", rule_type="drawdown_guard", mode="pct", level=10.0)
        await s.commit()
        return str(a.id), str(g.id)


async def _rule(own_session, rid):
    import uuid

    from app.models.tables import TradingRule
    async with own_session() as s:
        r = await s.get(TradingRule, uuid.UUID(rid))
        return (r.active, float(r.level)) if r else None


async def _delete(own_session, *rids):
    from app.services.feed_service import ensure_superadmin
    from app.services.rules_service import delete_rule
    async with own_session() as s:
        user = await ensure_superadmin(s)
        for rid in rids:
            await delete_rule(s, user, rid)


async def test_accept_pauses_the_rules_and_moves_the_guard(own_session):
    a, g = await _make_rules(own_session)
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            st0, st1 = _stats(0.5, 0.0, 33000, 22000), _stats(22.7, 0.8, 46000, 21000)
            await _store_verdict(own_session, _goal_verdict(cur, rules={
                "on": {**st0, "exits_per_year": [1.0], "pct_time_out": [49.0]},
                "off": {**_stats(32.9, 39.6), "exits_per_year": [0.0], "pct_time_out": [0.0]},
                "rebound": {**_stats(0.9, 0.0), "exits_per_year": [1.0], "pct_time_out": [27.0]},
                "effect_pts": -32.4, "fix_effect_pts": 0.4, "sleeves": {"btm_qqq_hold": {}},
                "tuning": {"material": True, "halve_floor_pct": 5.0,
                           "current": {"guard_pct": 10.0, "stats": st0},
                           "best": {"retire_sleeve_exits": True, "guard_pct": 15.0, "stats": st1},
                           "changes": {"pause_rule_ids": [a], "pause_rules": ["trailing_stop QQQ (10%)"],
                                       "guard": {"rule_id": g, "from_pct": 10.0, "to_pct": 15.0}}}}))
            card = next((x for x in _cards(c) if x["id"].startswith("rec_goal_protect_")), None)
            assert card is not None, [x["title"] for x in _cards(c)]
            assert "odds 0.5% -> 22.7%, halving 0% -> 0.8%" in card["title"]
            assert card["apply"]["kind"] == fs.RULES_CARD_KIND
            r = c.post(f"/api/v1/recommendations/{card['id']}/accept").json()
            assert r.get("applied") == fs.RULES_CARD_KIND, r
            assert await _rule(own_session, a) == (False, 10.0)          # paused, not deleted
            assert await _rule(own_session, g) == (True, 15.0)           # guard re-levelled
    finally:
        await _delete(own_session, a, g)
        await _reset_plan(own_session)


async def test_a_moved_rule_set_is_refused_untouched(own_session):
    from app.services.feed_service import ensure_superadmin
    a, g = await _make_rules(own_session)
    try:
        async with own_session() as s:
            user = await ensure_superadmin(s)
            out = await fs.apply_rules_tuning(s, user, {"pause_rule_ids": [a],
                                                       "guard": {"rule_id": g, "from_pct": 12.0, "to_pct": 15.0}})
        assert out["refused"] == "RULES_CHANGED"
        assert await _rule(own_session, a) == (True, 10.0)              # nothing was touched
        assert await _rule(own_session, g) == (True, 10.0)
    finally:
        await _delete(own_session, a, g)


def test_rules_spec_carries_the_ids_a_card_would_change():
    spec = fs.rules_spec([{"id": "t1", "ticker": "SOXL", "rule_type": "trailing_stop", "mode": "pct", "level": 20.0},
                          {"id": "g1", "ticker": "__BOOK__", "rule_type": "drawdown_guard", "mode": "pct",
                           "level": 10.0}],
                         prices={"SOXL": 100.0}, sleeve_tickers={"soxl": {"SOXL"}})
    assert spec["sleeves"]["soxl"]["exit_rule_ids"] == ["t1"]
    assert spec["guard"]["rule_id"] == "g1"
    assert pytest.approx(spec["guard"]["level"]) == 0.10
