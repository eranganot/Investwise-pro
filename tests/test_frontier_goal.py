"""PR 92 - the goal is the objective; the frontier and Kelly are how.

Eran, 2026-10-05: "the end goal is to meet the ROI goals the user set ... the
kelly parameter and the efficient frontier are tools and not the objectives."
Decisions: meeting the goal = at least a 70% chance; the goal picks the risk,
capped at full Kelly; the Kelly dial is an optional extra ceiling.
"""
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.engines import frontier as fr
from app.main import app
from app.services import frontier_service as fs

from tests.test_frontier_resize import EST_KW, _pos, _prep, _store_verdict


# --------------------------------------------------------------------------
# the goal maths
# --------------------------------------------------------------------------

def test_required_growth_lands_the_median_on_the_target():
    g = fr.required_log_growth(24_611, 82_692, 5.238)
    assert fr.median_wealth(24_611, g, 5.238) == pytest.approx(82_692, rel=1e-6)
    assert math.expm1(g) == pytest.approx(0.2603, abs=1e-3)      # the live book's 26.0%/yr
    with_deposits = fr.required_log_growth(24_611, 82_692, 5.238, monthly=500)
    assert with_deposits < g


def test_at_the_required_growth_the_odds_are_a_coin_flip():
    g = fr.required_log_growth(24_611, 82_692, 5.238)
    assert fr.goal_probability(24_611, 82_692, 5.238, g, 0.40) == pytest.approx(0.5, abs=1e-6)
    assert fr.goal_probability(24_611, 82_692, 5.238, g + 0.05, 0.40) > 0.5
    lo = fr.goal_probability(24_611, 82_692, 5.238, g, 0.40, monthly=0)
    hi = fr.goal_probability(24_611, 82_692, 5.238, g, 0.40, monthly=400)
    assert hi > lo


def test_the_rule_takes_the_safest_mix_that_reaches_70pct_else_the_best_odds():
    pts = [{"p": 0.80}, {"p": 0.74}, {"p": 0.71}, {"p": 0.55}]       # cap -> safest
    assert fs._goal_mix(pts, p_star=0.70) == (2, True)
    pts = [{"p": 0.40}, {"p": 0.46}, {"p": 0.30}]
    assert fs._goal_mix(pts, p_star=0.70) == (1, False)


def test_the_ceiling_is_full_kelly_unless_the_user_set_a_lower_one():
    assert fs.goal_ceiling(None) == 1.0
    assert fs.goal_ceiling(0.5) == 0.5
    assert fs.goal_ceiling(3.0) == 1.0


# --------------------------------------------------------------------------
# the solve
# --------------------------------------------------------------------------

def _goal(target, sleeves=None, nav=10_000, years=5.0, monthly=0.0, **kw):
    return fs.goal_solve(_prep(), sleeves_pct=sleeves or {"lev": 40.0, "calm": 3.0},
                         core_basket={"VTI": 0.6, "BND": 0.4}, cash_pct=4.0, objective="Grow",
                         est_kw=EST_KW, nav=nav, target=target, years=years, monthly=monthly,
                         draws=6, **kw)


def test_an_easy_goal_is_reached_without_buying_more_risk():
    # Even a small goal can fail 70% on an 85%-vol sleeve held at 40%: its
    # downside alone does it. The answer is then a SAFER mix, never a riskier one.
    v = _goal(target=10_500)
    assert v["outcome"] in (fs.GOAL_ON_TRACK, fs.GOAL_LESS_RISK, fs.GOAL_REBALANCE)
    assert max(v["before"]["goal_probability_pct"], v["after"]["goal_probability_pct"]) >= 70
    assert v["after"]["volatility_pct"] <= v["before"]["volatility_pct"] + 1e-9
    assert "extra_monthly_ils" not in v["levers"]


def test_a_comfortable_book_is_left_alone():
    v = _goal(target=10_500, sleeves={"lev": 2.0, "calm": 3.0})
    assert v["before"]["goal_probability_pct"] >= 70
    assert v["outcome"] in (fs.GOAL_ON_TRACK, fs.GOAL_LESS_RISK)


def test_an_impossible_goal_offers_levers_that_actually_reach_70pct():
    v = _goal(target=10_000_000)
    assert v["outcome"] in (fs.GOAL_OUT_OF_REACH, fs.GOAL_IMPROVE)
    lv = v["levers"]
    base = v["after"] if v["material"] else v["before"]
    g = base["log_growth"]
    vol = base["volatility_pct"] / 100
    m = lv["extra_monthly_ils"]
    # PR 93: the lever is the LARGER of the model's and history's deposit, so
    # "70%" holds on both; it is minimal on whichever of the two binds.
    assert m == max(lv["extra_monthly_model_ils"], lv["extra_monthly_history_ils"])
    assert fr.goal_probability(10_000, 10_000_000, 5.0, g, vol, monthly=m) >= 0.70 - 1e-6
    mm = lv["extra_monthly_model_ils"]
    assert fr.goal_probability(10_000, 10_000_000, 5.0, g, vol, monthly=mm - 10) < 0.70
    # the amount is the SMALLER of the two, and the model's own is exact
    assert lv["amount_at_confidence"] == min(lv["amount_at_confidence_model"], lv["amount_at_confidence_history"])
    assert fr.goal_probability(10_000, lv["amount_at_confidence_model"], 5.0, g, vol) == pytest.approx(0.70, abs=0.01)


def test_a_sleeve_may_grow_when_the_goal_needs_it():
    v = _goal(target=30_000, sleeves={"lev": 2.0, "calm": 2.0}, sleeve_cap_pct=40.0)
    grows = [s for s in v["sleeves"] if s["direction"] == "grow"]
    assert grows, v["sleeves"]
    assert all(s["proposed_pct"] <= 40.0 + 1e-9 for s in v["sleeves"])


def test_the_users_kelly_limit_caps_the_risk_the_goal_may_take():
    free = _goal(target=30_000, sleeves={"lev": 2.0, "calm": 2.0})
    capped = _goal(target=30_000, sleeves={"lev": 2.0, "calm": 2.0}, kelly_setting=0.25)
    assert capped["kelly_ceiling"] == 0.25 and free["kelly_ceiling"] == 1.0
    assert capped["after"]["volatility_pct"] <= free["after"]["volatility_pct"] + 1e-9


def test_no_goal_is_said_not_guessed():
    v = _goal(target=None)
    assert v["outcome"] == fs.GOAL_NO_TARGET


def test_a_growing_sleeve_sells_nothing_of_its_own():
    v = {"sleeves": [{"id": "s1", "current_pct": 10.0, "proposed_pct": 20.0, "act": True}],
         "core_basket": {"VTI": 0.6, "BND": 0.4}}
    t = fs.plan_trades(v, specs={"s1": {"weights": {"QQQ": 1.0}}},
                       holdings=[{"ticker": "QQQ", "shares": 10, "value_ils": 1000, "tax_ils": 50}])
    assert t["sells"] == [] and t["resizes"][0]["direction"] == "grow"


def test_the_roi_lever_reproduces_the_amount_through_the_goal_service():
    goal = {"flows": [{"at": "2026-08-03", "amount_ils": 20000}], "deadline": "2031-12-31"}
    roi = fs._roi_for_amount(goal, 45_834)
    from datetime import date

    from app.services.goal_service import compound
    got = compound([(date(2026, 8, 3), 20000.0)], roi / 100, date(2031, 12, 31))
    assert got == pytest.approx(45_834, rel=0.002)


# --------------------------------------------------------------------------
# the cards, accepted
# --------------------------------------------------------------------------

async def _reset_plan(own_session):
    from sqlalchemy import select

    from app.models.tables import Plan
    from app.services.feed_service import ensure_superadmin
    async with own_session() as s:
        user = await ensure_superadmin(s)
        plan = (await s.execute(select(Plan).where(Plan.user_id == user.id))).scalar_one_or_none()
        if plan is not None:
            plan.target_roi_pct = None
            plan.target_date = None
            plan.kelly_fraction = None
            plan.return_confidence = None
            await s.commit()


def _cards(c):
    j = c.get("/api/v1/recommendations").json()
    return j.get("recommendations", []) + j.get("more_recommendations", [])


def _goal_verdict(cur_pct, **over):
    v = {"ok": True, "mode": "goal", "outcome": fs.GOAL_OUT_OF_REACH, "material": False,
         "objective": "Grow", "p_star": 0.70, "kelly_ceiling": 1.0, "kelly_source": "goal (cap 1.0x)",
         "kelly_fraction": 1.0, "kelly_target": 1.0, "confidence": "shrunk",
         "goal": {"target": 82692.0, "years": 5.2, "nav": 24611.0, "monthly": 0.0,
                  "deadline": "2031-12-31", "roi_pct": 30.0, "target_date_raw": "2031",
                  "reachable_within_ceiling": False, "meets_now": False, "meets_after": False},
         "sleeves": [{"id": "btm_qqq_hold", "current_pct": cur_pct, "proposed_pct": cur_pct,
                      "point_pct": cur_pct, "band_pct": [0, 40], "act": False, "direction": None}],
         "core_basket": {"VTI": 0.6, "BND": 0.4}, "cash_pct": 0.0,
         "core_pct_before": 100 - cur_pct, "core_pct_after": 100 - cur_pct,
         "before": {"goal_probability_pct": 23.0, "median_at_deadline": 40724, "growth_cagr_pct": 10.1,
                    "volatility_pct": 41.9, "fraction_of_kelly": 1.14, "log_growth": 0.096},
         "after": {"goal_probability_pct": 23.0, "median_at_deadline": 40724, "growth_cagr_pct": 10.1,
                   "volatility_pct": 41.9, "fraction_of_kelly": 1.14, "log_growth": 0.096},
         "levers": {"required_cagr_pct": 26.0, "on_track_cagr_pct": 10.1, "extra_monthly_ils": 720,
                    "deadline_extra_years": 3.5, "amount_at_confidence": 45834.0,
                    "roi_at_confidence_pct": 15.0},
         "draws": 60, "window": {"sessions": 2215}, "version": fs.FRONTIER_TAG}
    v.update(over)
    return v


def _seed(c, qqq_sleeve_pct):
    for tk in ("SOXL", "SMH", "VTI", "BND", "TQQQ", "QQQ", "SCHD"):
        c.delete("/api/v1/portfolio/position", params={"ticker": tk, "market": "NASDAQ"})
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("QQQ", 10, 100, 80), _pos("VTI", 40, 100, 90), _pos("BND", 50, 100, 100, "Fixed Income")]})
    c.put("/api/v1/plan", json={"objective": "Grow", "target_roi_pct": 30, "target_date": "2031"})
    c.post(f"/api/v1/strategies/btm_qqq_hold/apply?sleeve_pct={qqq_sleeve_pct}")
    return float({s["strategy_id"]: s["sleeve_pct"]
                  for s in c.get("/api/v1/plan/sleeves").json()["sleeves"]}["btm_qqq_hold"])


async def test_out_of_reach_offers_actionable_levers_and_retires_the_old_goal_card(own_session):
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            await _store_verdict(own_session, _goal_verdict(cur))
            cards = _cards(c)
            kinds = {(x.get("meta") or {}).get("lever"): x for x in cards if x.get("dimension") == "goal"}
            assert {"deposit", "target", "deadline"} <= set(kinds), [x["title"] for x in cards]
            assert not [x for x in cards if x["id"].startswith("rec_") and "behind" in str(x.get("id"))
                        and x.get("dimension") == "goal" and not (x.get("meta") or {}).get("lever")]
            assert kinds["deposit"]["apply"] == {"kind": "set_standing_order", "monthly_total_ils": 720.0,
                                                 "add_ils": 720.0}
            assert kinds["target"]["apply"] == {"kind": "set_plan", "fields": {"target_roi_pct": 15.0}}
            assert kinds["deadline"]["apply"] == {"kind": "set_plan", "fields": {"target_date": "2035"}}
            assert "70% chance" in kinds["deposit"]["title"]

            r = c.post(f"/api/v1/recommendations/{kinds['deposit']['id']}/accept").json()
            assert r.get("ok") is True, r
            so = c.get("/api/v1/portfolio/standing-orders").json()
            orders = so.get("orders") if isinstance(so, dict) else so
            assert sum(float(o["amount_ils"]) for o in orders if o.get("active")) == pytest.approx(720.0)
            for o in orders:
                c.delete(f"/api/v1/portfolio/standing-orders/{o['id']}")
    finally:
        await _reset_plan(own_session)


async def test_a_goal_verdict_for_another_target_says_nothing(own_session):
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            await _store_verdict(own_session, _goal_verdict(cur))
            c.put("/api/v1/plan", json={"target_roi_pct": 25})
            assert not [x for x in _cards(c) if (x.get("meta") or {}).get("lever")]
    finally:
        await _reset_plan(own_session)


async def test_accepting_a_grow_card_funds_the_sleeve_and_records_every_share(own_session):
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            qqq_before = sum(float(p.get("value_ils") or 0) for p in c.get("/api/v1/portfolio").json()["positions"]
                             if p["ticker"] == "QQQ")
            v = _goal_verdict(cur, outcome=fs.GOAL_REBALANCE, material=True,
                              sleeves=[{"id": "btm_qqq_hold", "current_pct": cur, "proposed_pct": cur * 2,
                                        "point_pct": cur * 2, "band_pct": [cur * 1.5, cur * 2.5],
                                        "act": True, "direction": "grow"}],
                              after={"goal_probability_pct": 72.0, "median_at_deadline": 90000,
                                     "growth_cagr_pct": 27.0, "volatility_pct": 30.0,
                                     "fraction_of_kelly": 0.6, "log_growth": 0.24})
            await _store_verdict(own_session, v)
            card = next((x for x in _cards(c) if x["id"].startswith("rec_goal_mix_")), None)
            assert card is not None and "Rebalance to reach your goal" in card["title"]
            assert card["apply"]["resizes"][0]["direction"] == "grow"
            r = c.post(f"/api/v1/recommendations/{card['id']}/accept").json()
            assert r.get("applied") == fs.CARD_KIND, r
            after = {s["strategy_id"]: s["sleeve_pct"] for s in c.get("/api/v1/plan/sleeves").json()["sleeves"]}
            assert float(after["btm_qqq_hold"]) == pytest.approx(cur * 2)
            qqq_after = sum(float(p.get("value_ils") or 0) for p in c.get("/api/v1/portfolio").json()["positions"]
                            if p["ticker"] == "QQQ")
            assert qqq_after > qqq_before
            assert not r.get("unrecorded")
            c.post("/api/v1/strategies/btm_qqq_hold/apply?sleeve_pct=0")
    finally:
        await _reset_plan(own_session)


async def test_on_track_in_the_model_but_short_on_history_still_offers_levers(own_session):
    """PR 93: 'on track' must hold on real-market paths too."""
    try:
        with TestClient(app) as c:
            cur = _seed(c, 10)
            m = {"goal_probability_pct": 75.0, "median_at_deadline": 95000, "growth_cagr_pct": 28.0,
                 "volatility_pct": 30.0, "fraction_of_kelly": 0.6, "log_growth": 0.25}
            t = {"p_goal_pct": 62.0, "median": 88000, "worst5_mean": 9000, "p_halve_pct": 31.0,
                 "extra_monthly_needed": 210.0, "amount_at_confidence": 70000}
            await _store_verdict(own_session, _goal_verdict(
                cur, outcome=fs.GOAL_TAIL_SHORT, before=m, after=m,
                tail={"before": t, "after": t, "gate": "SHORT_OF_TARGET_ON_HISTORY", "paths": 2000},
                levers={"required_cagr_pct": 26.0, "on_track_cagr_pct": 28.0, "extra_monthly_ils": 210,
                        "extra_monthly_model_ils": 0, "extra_monthly_history_ils": 210,
                        "amount_at_confidence": 70000, "roi_at_confidence_pct": 27.0}))
            dep = next((x for x in _cards(c) if (x.get("meta") or {}).get("lever") == "deposit"), None)
            assert dep is not None
            assert "on real-market paths it is 62.0%" in dep["why"]
            assert "worst 5% of outcomes end near 9,000" in dep["why"]
            assert "history 210" in dep["why"]
    finally:
        await _reset_plan(own_session)


def test_settings_switch_to_goal_mode_when_the_plan_has_a_target():
    class P:
        objective, kelly_fraction, return_confidence, target_roi_pct = "Grow", None, None, 30.0
    s = fs.settings_of(P())
    assert s["mode"] == "goal" and s["kelly_fraction"] == 1.0 and s["goal_confidence"] == 0.70
    P.target_roi_pct = None
    assert fs.settings_of(P())["mode"] == "risk" and fs.settings_of(P())["kelly_fraction"] == 0.5
    assert np.isfinite(fs._z(0.3))
