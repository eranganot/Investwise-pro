"""PR 140 -- the protection retune may not switch off a stop (G1 of the 2026-10-22 review; D115).

Production, read 2026-10-22/23: "Retune your protection" (`rec_goal_protect_*` /
`rec_goal_protect_book`) was accepted on 10-06, 10-10, 10-11 and 10-20. Three
times a paused stop was re-armed and fired within minutes, already breached (SOXL
10-12 13:40 -> 13:42, 10-16 19:42 -> 19:46, 10-22 20:08 -> 20:22). On 10-22 the
owner re-armed about twenty rules by hand: stops and trailing stops on SOXL, TQQQ,
QQQ, VTI, VXUS, XLE, COMT, UGA and USO -- not only the leveraged sleeves.

Reproduced on HEAD `606ebec` before the fix (these two tests, inverted):

* the retune's one switch, `retire_sleeve_exits`, was all-or-nothing across every
  replay column -- sleeves AND (since PR 123) every held ticker with a rule;
* Accept (`apply_rules_tuning`) set `active = False` on each and wrote no rule
  event and no note, which is why PR 138 could not say who switched them off.

Owner, 2026-10-23: the card may switch off NO stop (it may still move the guard
and pause take-profits); paused rules get a standing heads-up with Re-arm (auto
re-arm is backlog); the card's title carries goal odds and halving side by side.
"""
import numpy as np
from fastapi.testclient import TestClient

from app.main import app
from app.services import frontier_service as fs

from tests.test_frontier_goal import _cards, _goal_verdict, _reset_plan, _seed
from tests.test_frontier_resize import _store_verdict


def _stats(p, h):
    return {"p_goal_pct": p, "p_halve_pct": h, "median": 40000, "worst5_mean": 20000}


def _book_rules():
    """A sleeve on a 3x fund and two plain holdings, each with an exit rule."""
    return fs.rules_spec(
        [{"id": "soxl-stop", "ticker": "SOXL", "rule_type": "stop_loss", "mode": "price", "level": 85.0},
         {"id": "soxl-trail", "ticker": "SOXL", "rule_type": "trailing_stop", "mode": "pct", "level": 20.0},
         {"id": "vti-stop", "ticker": "VTI", "rule_type": "stop_loss", "mode": "price", "level": 345.0},
         {"id": "xle-trail", "ticker": "XLE", "rule_type": "trailing_stop", "mode": "pct", "level": 10.0},
         {"id": "g1", "ticker": "__BOOK__", "rule_type": "drawdown_guard", "mode": "pct", "level": 25.0}],
        prices={"SOXL": 100.0, "VTI": 375.0, "XLE": 67.0},
        sleeve_tickers={"btm_trend_soxl": {"SOXL"}}, holding_tickers={"VTI", "XLE"})


def _columns(rules):
    """The replay columns the solve builds: one per sleeve, one per held ticker."""
    sids = list(rules["sleeves"]) + list(rules["holdings"])
    by = {**rules["sleeves"], **rules["holdings"]}
    sl = [{"col": i, "id": sid, "trail": by[sid]["trail"], "tp_ratio": by[sid]["tp_ratio"]}
          for i, sid in enumerate(sids)]
    return sl, sids


def test_the_retune_never_proposes_pausing_a_stop():
    """Even when retiring every exit would win by 20 points -- production's own
    numbers on 10-23 (0.3% with the rules, 21% without) -- it is not on offer."""
    rules = _book_rules()
    sl, sids = _columns(rules)

    def run(sl=None, gd="same", **kw):
        retired = sl is not None and all(s.get("trail") is None for s in sl)
        return _stats(21.0, 0.9) if retired else _stats(0.3, 0.0)
    t = fs._tune_protection(run, sl, rules["guard"] and {**rules["guard"], "col": 0}, rules, sids,
                            np.zeros((10, len(sids))), len(sids), _stats(0.3, 0.0), floor=30.0)
    assert not any(c["retire_sleeve_exits"] for c in t["candidates"])
    assert not set((t["changes"] or {}).get("pause_rule_ids") or []) & {
        "soxl-stop", "soxl-trail", "vti-stop", "xle-trail"}


async def test_accept_refuses_to_pause_a_stop_and_touches_nothing(own_session):
    """A spec from a verdict solved before PR 140 may still name a stop."""
    from app.models.tables import TradingRule
    from app.services.feed_service import ensure_user
    from app.services.rules_service import create_rule
    async with own_session() as s:
        user = await ensure_user(s, "pr140-refuse@example.com")
        stop = await create_rule(s, user, ticker="SOXL", rule_type="stop_loss", mode="price", level=85.0)
        guard = await create_rule(s, user, ticker="__BOOK__", rule_type="drawdown_guard", mode="pct", level=25.0)
        await s.commit()
        out = await fs.apply_rules_tuning(s, user, {
            "pause_rule_ids": [str(stop.id)],
            "guard": {"rule_id": str(guard.id), "from_pct": 25.0, "to_pct": 30.0}})
        assert out["ok"] is False and out["refused"] == "STOPS_STAY_ON", out
        assert "SOXL stop loss" in out["error"]
        await s.refresh(stop)
        await s.refresh(guard)
        assert stop.active is True                        # the stop stays on
        g = await s.get(TradingRule, guard.id)
        assert float(g.level) == 25.0                     # and the guard was not half-moved


async def test_a_pause_the_card_may_make_leaves_a_trace(own_session):
    """A take-profit pause: an event that names the card, and the rule's note."""
    from sqlalchemy import select

    from app.models.tables import RuleEvent
    from app.services.feed_service import ensure_user
    from app.services.rules_service import create_rule
    async with own_session() as s:
        user = await ensure_user(s, "pr140-trace@example.com")
        tp = await create_rule(s, user, ticker="SOXL", rule_type="take_profit", mode="pct", level=50.0)
        await s.commit()
        out = await fs.apply_rules_tuning(s, user, {"pause_rule_ids": [str(tp.id)]})
        assert out.get("rules_paused"), out
        await s.refresh(tp)
        ev = (await s.scalars(select(RuleEvent).where(RuleEvent.rule_id == str(tp.id)))).all()
        assert tp.active is False
        assert [(e.outcome, (e.action or {}).get("by")) for e in ev] == [("paused", "card")]
        assert tp.note.startswith("Paused by 'Retune your protection' on ")


async def test_your_own_pause_and_re_arm_leave_a_trace(own_session):
    from sqlalchemy import select

    from app.models.tables import RuleEvent
    from app.services.feed_service import ensure_user
    from app.services.rules_service import create_rule, toggle_rule
    async with own_session() as s:
        user = await ensure_user(s, "pr140-toggle@example.com")
        r = await create_rule(s, user, ticker="VTI", rule_type="stop_loss", mode="price", level=345.0)
        await s.commit()
        assert await toggle_rule(s, user, str(r.id))
        assert await toggle_rule(s, user, str(r.id))
        ev = (await s.scalars(select(RuleEvent).where(RuleEvent.rule_id == str(r.id))
                              .order_by(RuleEvent.created_at))).all()
        assert [(e.outcome, (e.action or {}).get("by")) for e in ev] == [("paused", "you"), ("resumed", "you")]


async def test_a_stale_verdict_that_would_pause_a_stop_shows_no_card(own_session):
    """Solved before PR 140 and still stored: the card stays off Today until the
    next solve, rather than offering an Accept that is refused."""
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            st0, st1 = _stats(0.5, 0.0), _stats(22.7, 0.8)
            await _store_verdict(own_session, _goal_verdict(cur, rules={
                "on": {**st0, "exits_per_year": [1.0], "pct_time_out": [49.0]},
                "off": {**_stats(32.9, 39.6), "exits_per_year": [0.0], "pct_time_out": [0.0]},
                "rebound": {**_stats(0.9, 0.0), "exits_per_year": [1.0], "pct_time_out": [27.0]},
                "effect_pts": -32.4, "fix_effect_pts": 0.4, "sleeves": {"btm_qqq_hold": {}},
                "tuning": {"material": True, "halve_floor_pct": 1.0,
                           "current": {"guard_pct": 10.0, "stats": st0},
                           "best": {"retire_sleeve_exits": True, "guard_pct": 10.0, "stats": st1},
                           "changes": {"pause_rule_ids": ["x"], "pause_rules": ["trailing_stop QQQ (10%)"],
                                       "guard": None}}}))
            assert not [x for x in _cards(c) if x["id"].startswith("rec_goal_protect_")]
    finally:
        await _reset_plan(own_session)


def test_switched_off_protection_is_one_headsup_with_re_arm_all():
    from app.services.recommendations import _protection_off_headsup

    def r(rid, tk, rt, active=False, breached=False):
        return {"id": rid, "ticker": tk, "rule_type": rt, "active": active, "breached_now": breached}
    rules = [r("a", "VTI", "stop_loss"), r("b", "XLE", "trailing_stop"), r("c", "SOXL", "take_profit"),
             r("d", "QQQ", "stop_loss", active=True), r("e", "SOXL", "stop_loss", breached=True),
             r("g", "__BOOK__", "drawdown_guard")]
    [h] = _protection_off_headsup(rules, carded={"e"})
    # take-profits are not downside protection; an armed rule is not off; a
    # crossed stop already has its own HIGH card (PR 138)
    assert h["meta"]["rule_ids"] == ["a", "b", "g"]     # by ticker; "__BOOK__" sorts last
    assert h["title"] == "3 protective rules are switched off"
    assert h["apply"] == {"kind": "none"}
    assert h["headsup_act"]["apply"] == {"kind": "rearm_rules", "rule_ids": ["a", "b", "g"]}
    # the id is the paused SET: a rule paused later brings a new heads-up
    [h2] = _protection_off_headsup(rules + [r("f", "UGA", "stop_loss")], carded={"e"})
    assert h2["id"] != h["id"]
    assert _protection_off_headsup([r("q", "QQQ", "stop_loss", active=True)], carded=set()) == []


async def test_re_arm_all_switches_them_back_on(own_session):
    from app.services.feed_service import ensure_superadmin
    from app.services.rules_service import create_rule, delete_rule, toggle_rule
    async with own_session() as s:
        user = await ensure_superadmin(s)
        a = await create_rule(s, user, ticker="VTI", rule_type="stop_loss", mode="pct", level=40.0)
        await s.commit()
        await toggle_rule(s, user, str(a.id))
        aid = str(a.id)
    try:
        with TestClient(app) as c:
            _seed(c, 10)
            body = c.get("/api/v1/recommendations").json()
            hs = [h for h in body.get("headsups") or []
                  if (h.get("card") or {}).get("meta", {}).get("headsup_kind") == "protection_off"]
            assert hs and aid in hs[0]["card"]["meta"]["rule_ids"], [h["title"] for h in body.get("headsups") or []]
            assert hs[0]["act"]["label"].startswith("Re-arm all")
            r = c.post(f"/api/v1/recommendations/{hs[0]['id']}/accept")
            assert r.status_code == 200, r.text
            rules = {x["id"]: x for x in c.get("/api/v1/rules").json()["rules"]}
            assert rules[aid]["active"] is True
    finally:
        async with own_session() as s:
            user = await ensure_superadmin(s)
            await delete_rule(s, user, aid)
        await _reset_plan(own_session)
