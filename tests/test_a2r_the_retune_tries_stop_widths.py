"""A2r (owner, 2026-10-24: "Widen up to x3"; rank "by confident amount").

On 10-24 the protection retune tried 28 setups -- the book guard and pausing take-profits --
and none moved the amount reached at the 65% bar by more than 300; every one read 0.1% goal
odds, which is what it ranked on. It never tried the lever that matters: how far the stops
sit. It may now WIDEN each exit (x1.5, x2, x3), never remove one (D115), and it ranks by the
amount reached at the bar.
"""
import uuid

import pytest

from app.services import frontier_service as fs
from tests.test_every_rule_shows_its_goal_impact import _rules
from tests.test_frontier_resize import EST_KW, _prep


def test_widening_moves_a_trail_and_a_fixed_stop_further_away():
    w = fs._widen({"trail": 0.10, "stop_ratio": 0.92}, 2.0)
    assert w["trail"] == pytest.approx(0.20) and w["stop_ratio"] == pytest.approx(0.84)
    assert fs._widen({"trail": 0.5}, 3.0)["trail"] == 0.9          # capped, never "off"
    rows = [{"id": "a", "ticker": "TQQQ", "rule_type": "trailing_stop", "mode": "pct", "level": 20.0},
            {"id": "b", "ticker": "SMH", "rule_type": "stop_loss", "mode": "price", "level": 500.0},
            {"id": "c", "ticker": "SMH", "rule_type": "take_profit", "mode": "price", "level": 600.0}]
    got = {x["rule_id"]: x["to_level"] for x in fs.widened_levels(rows, {"SMH": 525.0, "TQQQ": 66.0}, ["a", "b", "c"], 2.0)}
    assert got == {"a": 40.0, "b": 475.0}, "a take-profit is not an exit; 500 at 525 is 25 away -> 50 away"


def _solve():
    prep = _prep()
    return fs.goal_solve(prep, sleeves_pct={"lev": 30.0, "calm": 3.0}, core_basket={"VTI": 0.6, "BND": 0.4},
                         cash_pct=4.0, objective="Grow", est_kw=EST_KW, nav=10_000, target=25_000, years=5.0,
                         draws=0, rules=_rules(prep["ils"].shape[0]))


def test_the_retune_tries_wider_stops_and_ranks_by_the_amount_at_the_bar():
    tu = _solve()["rules"]["tuning"]
    assert tu["widths_measured"] is True
    wid = [c for c in tu["candidates"] if c.get("widen")]
    assert {f for c in wid for _sid, f in c["widen"]} == {1.5, 2.0, 3.0}
    assert not any(c["retire_sleeve_exits"] for c in tu["candidates"]), "D115: never off"
    full = [c for c in tu["candidates"] if c["paths"] is None and c["stats"]["p_halve_pct"] <= tu["halve_floor_pct"]]
    best = tu["best"]
    assert best["stats"]["amount_at_confidence"] == max(c["stats"]["amount_at_confidence"] for c in full)
    if tu["material"] and best.get("widen"):
        assert tu["changes"]["widen"] and all(w["to_level"] > w["from_level"] for w in tu["changes"]["widen"])


@pytest.mark.asyncio
async def test_accept_widens_the_stops_it_measured_and_refuses_if_one_moved(own_session):
    from sqlalchemy import select

    from app.models.tables import RuleEvent, TradingRule
    from app.services.feed_service import ensure_user
    async with own_session() as s:
        u = await ensure_user(s, "a2r@example.com")
        r = TradingRule(id=uuid.uuid4(), subject=u.email, ticker="TQQQ", rule_type="trailing_stop", mode="pct",
                        level=20.0, active=True)
        s.add(r)
        await s.commit()
        spec = {"widen": [{"rule_id": str(r.id), "ticker": "TQQQ", "rule_type": "trailing_stop", "mode": "pct",
                           "from_level": 20.0, "to_level": 40.0, "factor": 2.0}]}
        out = await fs.apply_rules_tuning(s, u, spec)
        assert out["rules_widened"][0]["to"] == 40.0
        await s.refresh(r)
        assert float(r.level) == 40.0 and r.active
        ev = (await s.execute(select(RuleEvent).where(RuleEvent.rule_id == str(r.id)))).scalars().all()
        assert any(e.outcome == "edited" and (e.action or {}).get("by") == "card" for e in ev)
        again = await fs.apply_rules_tuning(s, u, spec)                 # measured at 20, now 40
        assert again["ok"] is False and again["refused"] == "RULES_CHANGED"


async def test_the_card_lists_each_widened_stop_and_its_accept_carries_them(own_session):
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.test_frontier_goal import _cards, _goal_verdict, _reset_plan, _seed
    from tests.test_frontier_resize import _store_verdict
    from tests.test_pr140_protection_stays_on import _stats
    from app.services.feed_service import ensure_superadmin
    from app.services.rules_service import create_rule
    async with own_session() as s:
        user = await ensure_superadmin(s)
        tr = await create_rule(s, user, ticker="QQQ", rule_type="trailing_stop", mode="pct", level=20.0)
        await s.commit()
        tr = str(tr.id)
    wid = [{"rule_id": tr, "ticker": "QQQ", "rule_type": "trailing_stop", "mode": "pct",
            "from_level": 20.0, "to_level": 40.0, "factor": 2.0}]
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            st0 = {**_stats(0.1, 0.0), "amount_at_confidence": 25343.0}
            st1 = {**_stats(0.2, 0.6), "amount_at_confidence": 29000.0}
            await _store_verdict(own_session, _goal_verdict(cur, rules={
                "on": {**st0, "exits_per_year": [1.0], "pct_time_out": [49.0]},
                "off": {**_stats(23.0, 57.0), "exits_per_year": [0.0], "pct_time_out": [0.0]},
                "rebound": {**_stats(0.1, 0.0), "exits_per_year": [1.0], "pct_time_out": [27.0]},
                "effect_pts": -23.0, "fix_effect_pts": 0.0, "sleeves": {"btm_qqq_hold": {}},
                "tuning": {"material": True, "halve_floor_pct": 1.0, "tried": 40, "widths_measured": True,
                           "take_profits_measured": False,
                           "current": {"guard_pct": 25.0, "stats": st0},
                           "best": {"retire_sleeve_exits": False, "pause_take_profits": [], "guard_pct": 25.0,
                                    "guard_fraction_pct": 25.0, "guard_rearm_gap_pts": 5.0, "widen": [["btm_qqq_hold", 2.0]],
                                    "stats": st1},
                           "changes": {"pause_rule_ids": [], "pause_rules": [], "guard": None,
                                       "guard_settings": None, "widen": wid}}}))
            # the plan's halving limit in tests is 1% (the floor the verdict was solved under)
            [card] = [x for x in _cards(c) if x["id"] == "rec_goal_protect_book"]
            assert "Widen: QQQ trailing stop 20% -> 40%" in card["how"]
            assert card["apply"]["widen"] == wid
            assert "widen 1 stop (none is removed)" in card["action"]
            assert "25,343 -> 29,000" in card["impact"]
            r = c.post(f"/api/v1/recommendations/{card['id']}/accept").json()
            assert r.get("applied") == fs.RULES_CARD_KIND, r
            rule = next(x for x in c.get("/api/v1/rules").json()["rules"] if str(x["id"]) == tr)
            assert rule["active"] and float(rule["level"]) == 40.0
    finally:
        await _reset_plan(own_session)
