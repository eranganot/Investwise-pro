"""PR 77 (Eran, Q5): an actionable sell only with a measured edge over holding."""
from app.services.recommendations import _holding_review_recs
from tests._review_fixtures import down_both, measurement, row, short_dip


def _run(closes, meas, **kw):
    rows = [row("QQQX", 10.0, 150.0)]
    return _holding_review_recs(rows, {"nav": 10_000}, {"QQQX": closes},
                                {"QQQX": meas} if meas else {}, kw.get("sleeve", {}),
                                cash_ils=kw.get("cash", 500.0), trigger_ils=1500.0)


def test_exit_with_a_measured_edge_is_an_actionable_full_sale():
    review, cards = _run(down_both(), measurement())
    c = cards[0]
    assert review[0]["state"] == "Exit" and c["actionable"] is True and c["severity"] == "HIGH"
    assert c["apply"] == {"kind": "sell_position", "ticker": "QQQX", "shares": 10.0}
    assert "12.0%/yr" in c["why"] and "9.0%/yr holding" in c["why"]
    assert "Not financial advice." in c["how"]


def test_no_stop_is_offered_on_a_holding_that_already_has_one_armed():
    """PR 104b: BND's stop at 64.46 was armed and the card offered a second at 64.42."""
    rows = [row("QQQX", 10.0, 150.0)]
    _r, cards = _holding_review_recs(rows, {"nav": 10_000}, {"QQQX": down_both()}, {}, {},
                                     cash_ils=500.0, trigger_ils=1500.0, armed_stops={"QQQX"})
    assert not [c for c in cards if (c.get("apply") or {}).get("kind") == "create_rules"]
    _r, cards = _holding_review_recs(rows, {"nav": 10_000}, {"QQQX": down_both()}, {}, {},
                                     cash_ils=500.0, trigger_ils=1500.0)
    assert [c for c in cards if (c.get("apply") or {}).get("kind") == "create_rules"], "fixture sanity"


def test_trim_sells_the_versioned_fraction():
    _r, cards = _run(short_dip(), measurement())
    c = cards[0]
    assert c["apply"]["kind"] == "trim" and c["apply"]["shares"] == 5.0
    assert "review.trim_fraction@1" in " ".join(c["how"])


SELLS = ("trim", "sell_position")


def test_no_edge_never_sells_but_the_card_still_acts():
    """Eran, 2026-09-28: "the card should be actionable". It may not SELL
    without an edge (Q5); it arms a stop below today's price instead."""
    _r, cards = _run(down_both(), measurement(edge_long=False))
    c = cards[0]
    assert c["apply"]["kind"] not in SELLS
    assert c["actionable"] is True and c["apply"]["kind"] == "create_rules"
    (rule,) = c["apply"]["rules"]
    price = _r[0]["metrics"]["price"]
    assert rule["rule_type"] == "stop_loss" and rule["ticker"] == "QQQX"
    assert price * 0.85 - 0.01 <= rule["level"] <= price * 0.92 + 0.01, "8-15% below today"
    assert "did worse than holding" in c["action"]
    assert "Nothing is sold now" in c["impact"]


def test_a_stale_or_missing_measurement_never_sells():
    _r, stale = _run(down_both(), measurement(age_days=10))
    _r, missing = _run(down_both(), None)
    assert stale[0]["apply"]["kind"] not in SELLS and "10 days ago" in stale[0]["action"]
    assert missing[0]["apply"]["kind"] not in SELLS
    assert "not measured yet" in missing[0]["action"].lower()


def test_a_holding_in_an_uptrend_gets_no_card():
    from tests._review_fixtures import up
    review, cards = _run(up(), measurement())
    assert review[0]["state"] == "Hold" and cards == []


def test_an_unmeasurable_signal_is_not_reported_as_a_result():
    """Found in review: a ticker too young to backtest stored `measured: False`,
    and the card printed its reason as if it were a measured verdict."""
    meas = measurement()
    meas["long"] = {"measured": False, "edge": False,
                    "why": "230 overlapping sessions; need 260"}
    _r, cards = _run(down_both(), meas)
    c = cards[0]
    assert c["apply"]["kind"] not in SELLS
    assert "could not be measured (230 overlapping sessions; need 260)" in c["action"]
    assert "Historically" not in c["action"]


def test_a_missing_return_reads_n_a_not_none():
    """Found in review: ~251 bars leave the 12-month return undefined."""
    _r, cards = _run(down_both(n=251), measurement())
    assert "None" not in cards[0]["why"] and "12 months n/a" in cards[0]["why"]
