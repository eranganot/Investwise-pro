"""PR 70 -- the health score and the card priority, on Eran's live numbers.

Every input below is copied from production on 2026-09-22
(`C:\\dev\\_ship\\live-2026-09-22\\*.json`, NAV 23,426.31), and every expected
number is the one the approved proposal doc quoted from them. If a weight or a
cut-off changes, these fail -- deliberately: the doc Eran approved is the spec.

Not financial advice.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services import card_priority as cp
from app.services import plan_health as ph

NAV = 23426.31
TODAY = date(2026, 9, 22)


# --------------------------------------------------------------------------- #
# Health: On plan + On track
# --------------------------------------------------------------------------- #
def test_the_five_parts_on_the_live_book():
    sleeves, _ = ph.score_sleeves(
        [{"name": "SOXL", "target_pct": 40, "held_pct": 42.05},
         {"name": "QQQ", "target_pct": 19, "held_pct": 18.16}],
        core_pct=40.36, core_in_basket=0.13, band_pts=3.0)
    # Both sleeves inside their 3-point band; the core holds 13% of its basket.
    assert sleeves == pytest.approx((40 * 100 + 19 * 100 + 40.36 * 13) / 99.36, abs=0.01)
    assert ph.score_mix(6.14, 3.0) == pytest.approx(65.1, abs=0.1)
    assert ph.score_rules(protected=6, holdings=6, waiting=0) == 100
    assert ph.score_concentration(42.05, 40.0) == pytest.approx(59.0)
    assert ph.score_cash(461.48, 150.0, 1500.0) == 100


def test_the_score_is_the_mean_of_on_plan_and_on_track():
    parts = {"sleeves": 64.6, "mix": 65.1, "rules": 100.0, "concentration": 59.0, "cash": 100.0}
    res = ph.combine(parts, on_track=26.5)
    assert res["on_plan"] == 76 and res["on_track"] == 27
    assert res["score"] == 51 and res["label"] == "Needs attention"


def test_a_book_with_no_goal_is_scored_on_plan_alone():
    parts = dict.fromkeys(ph.WEIGHTS, 100.0)
    assert ph.combine(parts, on_track=None) == {"on_plan": 100, "on_track": None,
                                                 "score": 100, "label": "Strong"}


def test_risk_is_not_scored_against_an_outside_cap():
    """The old Risk chip read 3/100 because 49% volatility sat against a 25% cap.
    A book on its own plan scores on its plan: nothing here reads volatility."""
    parts = dict.fromkeys(ph.WEIGHTS, 100.0)
    assert ph.combine(parts, on_track=100.0)["score"] == 100


@pytest.mark.parametrize("cash,expected", [(75.0, 50.0), (150.0, 100.0), (1500.0, 100.0),
                                           (2250.0, 50.0), (3000.0, 0.0)])
def test_cash_between_the_floor_and_the_redeploy_line(cash, expected):
    assert ph.score_cash(cash, 150.0, 1500.0) == pytest.approx(expected)


def test_a_drift_band_is_forgiving_inside_and_zero_at_four_times():
    assert ph.band_score(3.0, 3.0) == 100
    assert ph.band_score(12.0, 3.0) == 0
    assert ph.band_score(-7.5, 3.0) == pytest.approx(50.0)


# --------------------------------------------------------------------------- #
# Card priority
# --------------------------------------------------------------------------- #
WEIGHTS = {"SOXL": 0.4205, "QQQ": 0.1816, "GOOGL": 0.2526}
MAX_W = {"SOXL": 42.0, "GOOGL": 40.0, "BND": 20.0}


def _idea(score, ret, frm=0.0, to=5.0, tgt=10.0):
    return {"screen_score": score, "return_6m_pct": ret,
            "class_from_pct": frm, "class_to_pct": to, "class_target_pct": tgt}


LIVE = [
    {"id": "rec_goal", "dimension": "goal", "est_amount": 1150.0,
     "apply": {"kind": "set_standing_order"}},
    {"id": "rec_trim", "dimension": "diversification", "est_amount": 481.26,
     "apply": {"kind": "none"}, "meta": {"ticker": "SOXL", "source": "plan_cap"}},
    {"id": "rec_rebal", "dimension": "allocation", "est_amount": 1436.22,
     "apply": {"kind": "rebalance_to_objective"}},
    {"id": "rec_core", "dimension": "allocation", "est_amount": 773.74,
     "apply": {"kind": "core_to_basket"}},
    {"id": "rec_uso", "dimension": "opportunity", "est_amount": 1121.47,
     "apply": {"kind": "buy_funded"}, "meta": {"ticker": "USO", "idea": _idea(85.4, 24.7)}},
    {"id": "rec_uga", "dimension": "opportunity", "est_amount": 1121.47,
     "apply": {"kind": "buy_funded"}, "meta": {"ticker": "UGA", "idea": _idea(75.0, 43.0)}},
]


def _scored():
    cards = [dict(c) for c in LIVE]
    cp.apply_priority(cards, nav=NAV, weights=WEIGHTS, max_weight_rules=MAX_W, today=TODAY)
    return {c["id"]: c for c in cards}


@pytest.mark.parametrize("cid,score,label", [
    ("rec_uso", 51.9, "Worth doing"),      # was Minor
    ("rec_trim", 51.4, "Worth doing"),
    ("rec_goal", 50.0, "Worth doing"),     # was Important
    ("rec_uga", 50.4, "Worth doing"),      # was Minor
    ("rec_rebal", 40.0, "Nice to have"),
    ("rec_core", 26.4, "Minor"),           # was Nice to have
])
def test_todays_cards_score_as_the_doc_said(cid, score, label):
    p = _scored()[cid]["priority"]
    assert p["score"] == pytest.approx(score, abs=0.15), p
    assert p["label"] == label


def test_a_close_call_goes_to_your_own_rule():
    """USO 51.9 vs the SOXL trim 51.4: within 5 points, the card at your own
    42% max-weight rule sorts first."""
    cards = list(_scored().values())
    cards.sort(key=cp.sort_key)
    assert [c["id"] for c in cards][:2] == ["rec_trim", "rec_uso"]


def test_a_fired_rule_is_important():
    """The SOXL "43% of your portfolio" card: rule fired, about 328 ILS."""
    p = cp.score_card({"id": "rule_1a2b3c4d", "dimension": "rule", "est_amount": 328.0,
                       "meta": {"rule_type": "max_weight"}}, nav=NAV)
    assert p["score"] == pytest.approx(71.2, abs=0.1) and p["label"] == "Important"


@pytest.mark.parametrize("dist,amount,score,label", [
    (4.64, 2462.95, 55.0, "Worth doing"),   # SOXL take-profit
    (7.39, 1479.59, 50.0, "Worth doing"),   # GOOGL
    (7.03, 1063.55, 46.3, "Nice to have"),  # QQQ
    (9.72, 271.90, 19.3, "Minor"),          # AGG
])
def test_approaching_a_take_profit_scales_with_distance_and_money(dist, amount, score, label):
    p = cp.score_card({"id": "rec_x", "dimension": "holding", "est_amount": amount,
                       "meta": {"rule_type": "take_profit", "distance_pct": dist}}, nav=NAV)
    assert p["score"] == pytest.approx(score, abs=0.15) and p["label"] == label


def test_a_stop_within_two_percent_is_always_important():
    p = cp.score_card({"id": "rec_s", "dimension": "holding", "est_amount": 50.0,
                       "meta": {"rule_type": "trailing_stop", "distance_pct": 1.5}}, nav=NAV)
    assert p["score"] < 50 and p["severity"] == "CRITICAL"


def test_an_idea_for_a_class_already_at_target_gets_no_fit_points():
    q = cp.quality_points(_idea(85.4, 24.7, frm=12.0, to=15.0, tgt=10.0))
    assert q == pytest.approx(10.0 + 12.81, abs=0.01)


def test_the_label_is_written_back_into_severity():
    for c in _scored().values():
        assert c["severity"] == c["priority"]["severity"]


def test_two_ideas_for_one_gap_become_one_card():
    cards = [dict(c, meta={**c["meta"], "asset_class": "Commodities"}) if c.get("meta", {}).get("idea")
             else dict(c) for c in LIVE]
    cp.apply_priority(cards, nav=NAV, weights=WEIGHTS, max_weight_rules=MAX_W, today=TODAY)
    out = cp.fold_alternatives(cards)
    ideas = [c for c in out if (c.get("meta") or {}).get("idea")]
    assert [c["id"] for c in ideas] == ["rec_uso"]
    assert [a["ticker"] for a in ideas[0]["alternatives"]] == ["UGA"]
    assert len(out) == len(LIVE) - 1
