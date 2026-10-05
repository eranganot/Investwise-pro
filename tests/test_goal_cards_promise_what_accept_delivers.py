"""PR 99: a goal card only promises what Accept delivers.

The goal is "30% a year on every shekel put in", so accepting "add 680/month"
or "move the deadline to 2039" makes the goal itself bigger. The levers were
solved with the goal frozen: the cards said 70%, the real chance after Accept
was 30.4% and 26.9% -- lower than today's 38.1%.
"""
from datetime import date

import pytest

from app.engines import frontier as fr
from app.services import frontier_service as fs
from app.services.goal_service import compound
from tests.test_frontier_resize import EST_KW, _prep

RULES = {"sleeves": {"lev": {"trail": 0.2, "tp_ratio": 1.13, "tp_fraction": 0.25}},
         "guard": {"level": 0.10, "rearm": 0.075, "fraction": 0.25}, "not_simulated": []}


def _solve(target, *, goal_roi=None, rules=None, years=5.0, sleeves=None):
    return fs.goal_solve(_prep(), sleeves_pct=sleeves or {"lev": 40.0, "calm": 3.0},
                         core_basket={"VTI": 0.6, "BND": 0.4},
                         cash_pct=4.0, objective="Grow", est_kw={**EST_KW, "confidence": "blended"},
                         nav=10_000, target=target, years=years, draws=0, rules=rules, goal_roi=goal_roi)


def test_a_deposit_adds_to_the_goal_exactly_as_the_goal_formula_counts_it():
    # 680/month for 13 years at 30%, deposited each month's end, through the
    # same compound() the Plan tab derives the goal with.
    years, m, roi = 13.0, 680.0, 0.30
    to = date(2039, 12, 28)
    n = int(years * 12)
    end = to.year * 12 + to.month - 1
    flows = [(date(*divmod(end - (n - k), 12)[:1], divmod(end - (n - k), 12)[1] + 1, 28), m)
             for k in range(1, n + 1)]
    via_goal = compound(flows, roi, to)
    assert fs._fv_monthly(m, roi, years) == pytest.approx(via_goal, rel=0.01)


def test_a_roi_goal_the_book_cannot_earn_gets_no_money_or_time_cards():
    nav, years, roi = 10_000, 5.0, 0.30
    v = _solve(nav * (1 + roi) ** years, goal_roi=roi, years=years)
    lv, base = v["levers"], v["after"] if v["material"] else v["before"]
    assert base["growth_cagr_pct"] < 30, "the fixture book is expected to earn less than the goal asks"
    assert lv["sized_p_goal_pct"] < 70
    # Frozen, a deposit WOULD have reached 70% (the old cards' arithmetic) ...
    frozen = fs.goal_solve(_prep(), sleeves_pct={"lev": 40.0, "calm": 3.0}, core_basket={"VTI": 0.6, "BND": 0.4},
                           cash_pct=4.0, objective="Grow", est_kw={**EST_KW, "confidence": "blended"},
                           nav=nav, target=nav * (1 + roi) ** years, years=years, draws=0)
    assert frozen["levers"]["extra_monthly_ils"]
    # ... but every deposit has to grow 30% too, so none does, and no later year either.
    assert lv["extra_monthly_ils"] is None and lv["deadline_extra_years"] is None
    assert lv["money_time_note"] == {"roi_pct": 30.0, "expected_cagr_pct": base["growth_cagr_pct"]}
    m = frozen["levers"]["extra_monthly_ils"]
    grown = nav * (1 + roi) ** years + fs._fv_monthly(m, roi, years)
    p_real = fr.goal_probability(nav, grown, years, base["log_growth"], base["volatility_pct"] / 100, m)
    assert p_real < 0.70 and p_real * 100 < base["goal_probability_pct"], \
        "the frozen card's deposit LOWERS the real chance"


def test_a_lever_that_is_offered_reaches_70_on_the_goal_after_accept():
    # A modest ROI goal a calm book can out-earn: a deposit helps, and its
    # card's goal is the grown one.
    nav, years, roi = 10_000, 15.0, 0.0
    v = _solve(nav * 3.0, goal_roi=roi, years=years, sleeves={"lev": 2.0, "calm": 3.0})
    lv, base = v["levers"], v["after"] if v["material"] else v["before"]
    if lv["sized_p_goal_pct"] >= 70:
        pytest.skip("on track already")
    m = lv["extra_monthly_ils"]
    assert m and lv["deposit_goal_after"] == round(nav * 3.0 + fs._fv_monthly(m, roi, years), 0)
    assert fr.goal_probability(nav, lv["deposit_goal_after"], years, base["log_growth"],
                               base["volatility_pct"] / 100, m) >= 0.70 - 1e-6


def test_the_headline_is_the_rules_replay_and_suggestions_are_sized_on_it():
    v = _solve(30_000, rules=RULES)
    rb, hl, lv = v["rules"], v["headline"], v["levers"]
    assert hl == {"p_goal_pct": rb["on"]["p_goal_pct"], "without_rules_pct": rb["off"]["p_goal_pct"],
                  "basis": "rules"}
    tu = rb.get("tuning") or {}
    want = "after_retune" if tu.get("material") else "rules_as_they_are"
    assert lv["sized_on"] == want
    sized = tu["best"]["stats"] if want == "after_retune" else rb["on"]
    assert lv["sized_p_goal_pct"] == sized["p_goal_pct"]
    if "amount_at_confidence" in lv:
        assert lv["amount_at_confidence"] <= sized["amount_at_confidence"]


def test_without_rules_the_headline_is_the_lower_of_model_and_history():
    v = _solve(30_000)
    assert v["headline"]["basis"] == "model_and_history"
    assert v["headline"]["p_goal_pct"] == min(v["before"]["goal_probability_pct"],
                                              v["tail"]["before"]["p_goal_pct"])
    assert v["headline"]["without_rules_pct"] is None
