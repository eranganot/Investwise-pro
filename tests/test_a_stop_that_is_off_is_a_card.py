"""2026-10-22 -- "why no recommendation card while my portfolio dropped?"

SOXL fell 20% in a session with its stop-loss switched OFF below the stop level.
An inactive rule raised nothing, and no card read a single day's move.
Facts only: Accept switches the stop on; the big-move line is a heads-up.
"""
from types import SimpleNamespace as NS

from app.services.recommendations import _off_stop_and_big_move_recs as gen

ROWS = [NS(ticker="SOXL", current_price=82.39)]
SNAP = {"nav": 20000.0, "exposure_ticker": {"SOXL": 0.1}}


def _rule(**kw):
    base = dict(id="11111111-1111-1111-1111-111111111111", ticker="SOXL",
                rule_type="stop_loss", active=False, breached_now=True,
                current_price=82.39, level=83.85, target=83.85)
    base.update(kw)
    return base


def test_an_off_crossed_stop_is_an_actionable_card():
    (c,) = gen(ROWS, SNAP, [_rule()], {})
    assert c["severity"] == "HIGH" and "SOXL" in c["title"] and "switched off" in c["title"]
    assert c["apply"] == {"kind": "edit_rule", "target_rule_id": _rule()["id"]}
    assert "sell" not in c["title"].lower()
    assert c["est_amount"] == 2000.0


def test_no_card_when_the_stop_is_on_or_not_crossed():
    assert gen(ROWS, SNAP, [_rule(active=True)], {}) == []
    assert gen(ROWS, SNAP, [_rule(breached_now=False)], {}) == []
    assert gen(ROWS, SNAP, [_rule(rule_type="take_profit")], {}) == []


def test_a_big_day_move_is_a_non_actionable_headsup():
    cards = gen(ROWS, SNAP, [], {"SOXL": [100.0, 103.0]})
    (c,) = cards
    assert c["apply"] == {"kind": "none"} and "fell" in c["title"]
    assert c["meta"]["day_move_pct"] < -10


def test_a_quiet_day_says_nothing():
    assert gen(ROWS, SNAP, [], {"SOXL": [82.0, 82.4]}) == []
    assert gen(ROWS, SNAP, [], {"SOXL": [82.0]}) == []
