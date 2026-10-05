"""PR 100: every number says what it measures, without making cards harder to read.

No card gets a new sentence: each fix deletes a false one or replaces it with a
shorter true one.
"""
from datetime import datetime, timedelta, timezone

from app.services import recommendations as rec
from tests.test_the_app_says_when_to_take_profits import _P, _rule

NAV = 24_636.0
# SOXL as on 2026-10-05: +36% since bought, 33% of the book, 40% plan target.
SOXL = _P("SOXL", 4.6, 177.0, 130.0)
SNAP = {"nav": NAV, "exposure_ticker": {"SOXL": 0.33}}
TP = [_rule("SOXL", "take_profit", 183.17, target=183.17)]


def _gain_card(cards):
    return next(c for c in cards if c.get("id") == rec._rid("ran_up", "SOXL"))


def test_under_its_target_a_gain_is_one_line_and_suggests_no_trim():
    c = _gain_card(rec._profit_recs([SOXL], SNAP, TP, {"SOXL": 0.40}))
    assert c["title"].startswith("SOXL +36% since you bought:") and "of gain" in c["title"]
    assert c["action"] == "Your take-profit at 183.17 covers it."
    assert c["how"] == ["Your take-profit raises a card at 183.17"] and not c.get("why") and not c.get("impact")
    assert "trim" not in (c["title"] + c["action"]).lower()


def test_without_a_known_target_a_gain_suggests_no_trim_either():
    c = _gain_card(rec._profit_recs([SOXL], SNAP, [], None))
    assert c["action"] == "You have no take-profit on it."
    assert "trim" not in (c["title"] + c["action"]).lower()


def test_over_its_target_the_card_is_a_trim_to_target_sized_on_the_excess():
    c = _gain_card(rec._profit_recs([SOXL], {"nav": NAV, "exposure_ticker": {"SOXL": 0.48}}, TP, {"SOXL": 0.40}))
    assert c["title"] == "Trim SOXL to target: 48% of your book vs 40%"
    assert c["est_amount"] == round(0.08 * NAV, 2)
    assert c["meta"]["over_target_ils"] == c["est_amount"]


def test_a_card_with_nothing_specific_to_say_says_nothing():
    out = rec._ensure_why_impact([{"id": "x", "dimension": "holding", "title": "t", "action": "a"}])
    assert out[0]["why"] == "" and out[0]["impact"] == ""
    assert out[0]["audit_trail"], "the audit trail is still built"


def test_only_a_card_that_asked_for_a_trade_can_say_there_is_money_still_to_move():
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    entry = {"at": old, "amount": 2154.67, "severity": "MEDIUM"}
    gain = {"severity": "MEDIUM", "est_amount": 2154.67, "apply": {"kind": "none"}}
    assert rec.moved_on(entry, gain, done=True) is None
    trade = {"severity": "MEDIUM", "est_amount": 2154.67, "apply": {"kind": "trim", "ticker": "SOXL"}}
    why = rec.moved_on(entry, trade, done=True)
    assert why and "to move" in why
