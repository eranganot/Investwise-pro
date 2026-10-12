"""PR 121 - the protection retune searches properly.

The goal card said "this is already the best chance that keeps halving at or
under your limit" on the strength of two setups (guard 15% vs 20%), and that
take-profits "measured neutral" when nothing re-measured them. The search now
crosses the sleeve exits, each take-profit, and the guard's level x sell size x
re-arm gap; every value tried is a setting; and the sentences claim only what
was measured.
"""
from types import SimpleNamespace

import numpy as np
from fastapi.testclient import TestClient

from app.main import app
from app.services import advanced_rules, thresholds
from app.services import frontier_service as fs

from tests.test_frontier_goal import _cards, _goal_verdict, _reset_plan, _seed
from tests.test_protection_tuning import _delete, _rule, _stats
from tests.test_frontier_resize import _store_verdict

GUARD = {"level": 0.15, "rearm": 0.125, "fraction": 0.25, "col": 0}


def _tune(run, sl, rules, sleeve_ids=("qqq",), guard=GUARD, r_on=None, floor=1.0):
    r_on = r_on or run(sl=sl, gd=guard)
    return fs._tune_protection(run, sl, guard, rules, list(sleeve_ids), np.zeros((10, 1)), 1, r_on, floor=floor)


def test_a_take_profit_that_costs_the_goal_is_paused():
    sl = [{"col": 0, "trail": None, "tp_ratio": 1.06, "tp_fraction": 0.25}]
    rules = {"sleeves": {"qqq": {"exit_rule_ids": [], "tp_rule_ids": ["tp1"],
                                 "rules": ["take_profit QQQ (+6%, sells 25%)"]}}, "guard": {"rule_id": "g1"}}

    def run(sl=None, gd=None, **kw):
        return _stats(20.0 if sl[0].get("tp_ratio") else 26.0, 0.5)
    t = _tune(run, sl, rules)
    assert t["material"] and t["best"]["pause_take_profits"] == ["qqq"]
    assert t["changes"]["pause_rule_ids"] == ["tp1"]
    assert t["changes"]["pause_rules"] == ["take_profit QQQ (+6%, sells 25%)"]
    assert t["changes"]["guard"] is None and t["changes"]["guard_settings"] is None
    assert t["take_profits_measured"] is True


def test_guard_17_5_beats_15_under_the_limit_and_is_proposed():
    by_level = {15.0: _stats(10.0, 0.2), 17.5: _stats(16.0, 0.8), 20.0: _stats(18.0, 1.5), 25.0: _stats(22.0, 3.0)}

    def run(sl=None, gd=None, **kw):
        return by_level[round(gd["level"] * 100, 2)]
    t = _tune(run, [{"col": 0, "trail": None}], {"sleeves": {}, "guard": {"rule_id": "g1"}})
    assert t["best"]["guard_pct"] == 17.5                       # 20% and 25% halve too often
    assert t["changes"]["guard"] == {"rule_id": "g1", "from_pct": 15.0, "to_pct": 17.5}
    assert t["changes"]["guard_settings"] is None               # the tie keeps today's sell size and gap


def test_the_guard_sell_size_and_re_arm_gap_are_searched():
    def run(sl=None, gd=None, **kw):
        better = abs(gd["fraction"] - 0.5) < 1e-9 and abs(gd["rearm"] - 0.10) < 1e-9   # sells 50%, gap 5
        return _stats(18.0 if better else 10.0, 0.5)
    t = _tune(run, [{"col": 0, "trail": None}], {"sleeves": {}, "guard": {"rule_id": "g1"}})
    assert t["material"] and t["changes"]["guard"] is None
    assert t["changes"]["guard_settings"] == {"from": {"fraction_pct": 25.0, "rearm_gap_pts": 2.5},
                                              "to": {"fraction_pct": 50.0, "rearm_gap_pts": 5.0}}


def test_the_grid_is_wide_capped_and_finalists_are_re_measured_in_full():
    calls = []

    def run(sl=None, gd=None, n_paths=None, **kw):
        calls.append(n_paths)
        return _stats(10.0, 0.5)
    sl = [{"col": 0, "trail": 0.2, "tp_ratio": 1.1}]
    t = _tune(run, sl, {"sleeves": {}, "guard": {}}, r_on=_stats(10.0, 0.5))
    # 4 structures (exits x take-profit) + 4 levels x 2 sizes x 2 gaps, minus today's counted twice
    assert t["tried"] == len(t["candidates"]) >= 4 + 16 - 1
    assert t["tried"] <= fs.RETUNE_MAX_SETUPS
    assert calls.count(fs.RETUNE_SCREEN_PATHS) == t["tried"]
    assert calls.count(None) == fs.RETUNE_FINALISTS             # the finalists, on every path
    assert sum(1 for c in t["candidates"] if c["current"]) == 1


def test_the_sentences_claim_only_what_was_measured():
    src = open(fs.__file__, encoding="utf-8").read()
    assert "measured neutral" not in src and "Every setup" not in src
    tu = {"tried": 17, "take_profits_measured": False, "halve_floor_pct": 1.0}
    assert fs._setups_replayed(tu).startswith("17 setups")
    assert fs._take_profit_line(tu, {"pause_rules": []}) == ""
    assert "measured" in fs._take_profit_line({**tu, "take_profits_measured": True}, {"pause_rules": []})
    html = open("app/static_app/index.html", encoding="utf-8").read()
    assert "rb.tuning.tried" in html


def test_the_new_dials_default_to_todays_behaviour_and_the_live_guard_reads_them():
    d = thresholds.defaults()
    assert d["guard_sell_fraction_pct"] == 25.0 and d["guard_rearm_gap_pts"] == 2.5
    assert d["guard_fractions_tried"] == [25.0, 50.0] and d["guard_rearm_gaps_tried"] == [2.5, 5.0]
    rule = SimpleNamespace(rule_type="drawdown_guard", level=15.0)
    pos = {"book": {"dd_pct": 11.0}}
    assert advanced_rules.derisk_fraction() == 0.25
    assert advanced_rules.clears(rule, pos)                      # re-arms under 12.5%
    try:
        thresholds.use(SimpleNamespace(thresholds={"guard_sell_fraction_pct": 50, "guard_rearm_gap_pts": 5}))
        assert advanced_rules.derisk_fraction() == 0.5
        assert not advanced_rules.clears(rule, pos)              # now re-arms under 10%
        spec = fs.rules_spec([{"id": "g", "ticker": "__BOOK__", "rule_type": "drawdown_guard", "mode": "pct",
                               "level": 15.0}], prices={}, sleeve_tickers={})
        assert spec["guard"]["fraction"] == 0.5 and abs(spec["guard"]["rearm"] - 0.10) < 1e-9
    finally:
        thresholds._OVERRIDES.set(None)


def test_rules_spec_carries_the_take_profit_ids():
    spec = fs.rules_spec([{"id": "tp1", "ticker": "QQQ", "rule_type": "take_profit", "mode": "pct", "level": 6.0}],
                         prices={"QQQ": 500.0}, sleeve_tickers={"qqq": {"QQQ"}})
    assert spec["sleeves"]["qqq"]["tp_rule_ids"] == ["tp1"] and spec["sleeves"]["qqq"]["exit_rule_ids"] == []


async def _plan_thresholds(own_session, value=None, read=False):
    from sqlalchemy import select

    from app.models.tables import Plan
    from app.services.feed_service import ensure_superadmin
    async with own_session() as s:
        user = await ensure_superadmin(s)
        plan = (await s.execute(select(Plan).where(Plan.user_id == user.id))).scalar_one_or_none()
        if read:
            return dict((plan.thresholds if plan else None) or {})
        if plan is not None:
            plan.thresholds = value
            await s.commit()


async def test_accept_pauses_a_take_profit_and_moves_the_guard_dials(own_session):
    from app.services.feed_service import ensure_superadmin
    from app.services.rules_service import create_rule
    async with own_session() as s:
        user = await ensure_superadmin(s)
        tp = await create_rule(s, user, ticker="QQQ", rule_type="take_profit", mode="pct", level=6.0)
        g = await create_rule(s, user, ticker="__BOOK__", rule_type="drawdown_guard", mode="pct", level=15.0)
        await s.commit()
        tp, g = str(tp.id), str(g.id)
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            st0, st1 = _stats(10.0, 0.5, 33000, 22000), _stats(16.0, 0.6, 41000, 21000)
            gs = {"from": {"fraction_pct": 25.0, "rearm_gap_pts": 2.5}, "to": {"fraction_pct": 50.0, "rearm_gap_pts": 5.0}}
            await _store_verdict(own_session, _goal_verdict(cur, rules={
                "on": {**st0, "exits_per_year": [0.0], "pct_time_out": [0.0]},
                "off": {**_stats(12.0, 9.0), "exits_per_year": [0.0], "pct_time_out": [0.0]},
                "rebound": {**st0, "exits_per_year": [0.0], "pct_time_out": [0.0]},
                "effect_pts": -2.0, "fix_effect_pts": 0.0, "sleeves": {"btm_qqq_hold": {}},
                "tuning": {"material": True, "halve_floor_pct": 1.0, "tried": 23, "take_profits_measured": True,
                           "current": {"guard_pct": 15.0, "stats": st0},
                           "best": {"retire_sleeve_exits": False, "pause_take_profits": ["btm_qqq_hold"],
                                    "guard_pct": 15.0, "guard_fraction_pct": 50.0, "guard_rearm_gap_pts": 5.0,
                                    "stats": st1},
                           "changes": {"pause_rule_ids": [tp], "pause_rules": ["take_profit QQQ (+6%, sells 25%)"],
                                       "guard": None, "guard_settings": gs}}}))
            card = next((x for x in _cards(c) if x["id"].startswith("rec_goal_protect_")), None)
            assert card is not None, [x["title"] for x in _cards(c)]
            assert "23 setups" in card["why"] and "measured neutral" not in card["why"]
            assert "pause 1 take-profit" in card["action"] and "sell 50% (was 25%)" in card["action"]
            assert card["apply"]["guard_settings"] == gs
            r = c.post(f"/api/v1/recommendations/{card['id']}/accept").json()
            assert r.get("applied") == fs.RULES_CARD_KIND, r
            assert await _rule(own_session, tp) == (False, 6.0)          # the take-profit is paused
            assert await _rule(own_session, g) == (True, 15.0)           # the guard's level is untouched
            th = await _plan_thresholds(own_session, read=True)
            assert th.get("guard_sell_fraction_pct") == 50.0 and th.get("guard_rearm_gap_pts") == 5.0
    finally:
        await _plan_thresholds(own_session, None)
        await _delete(own_session, tp, g)
        await _reset_plan(own_session)


async def test_moved_guard_dials_are_refused_untouched(own_session):
    from app.services.feed_service import ensure_superadmin
    from app.services.rules_service import create_rule
    async with own_session() as s:
        user = await ensure_superadmin(s)
        tp = await create_rule(s, user, ticker="QQQ", rule_type="take_profit", mode="pct", level=6.0)
        await s.commit()
        tp = str(tp.id)
    try:
        async with own_session() as s:
            user = await ensure_superadmin(s)
            out = await fs.apply_rules_tuning(s, user, {"pause_rule_ids": [tp], "guard_settings": {
                "from": {"fraction_pct": 40.0, "rearm_gap_pts": 2.5}, "to": {"fraction_pct": 50.0, "rearm_gap_pts": 5.0}}})
        assert out["refused"] == "RULES_CHANGED"
        assert await _rule(own_session, tp) == (True, 6.0)              # nothing was touched
        assert not (await _plan_thresholds(own_session, read=True)).get("guard_sell_fraction_pct")
    finally:
        await _delete(own_session, tp)
