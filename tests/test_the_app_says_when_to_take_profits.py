"""Cards for taking money off the table, built from the user's own levels.

Eran, 2026-09-14: *"I'm holding assets that are moving which is great but I
don't see any card recommendation about when to sell to trim profits."*

He had none, and not because of a bug. Measured on his book that day:

  * the only ACTIONABLE trim in the app was a concentration-cap breach;
  * `Trim: SOXL` was a FUNDAMENTALS verdict (fires at score < 40), mentioned a
    gain only as a clause in its sentence, and shipped `apply: {"kind":"none"}`
    with `est_amount: None`;
  * his take-profit on SOXL sits at **+80%** (target 201.39 against 121.82), so
    nothing was ever going to fire;
  * and SOXL was **37.8% of the book against a 38% cap he set himself** -- two
    tenths of a point from breaching, with the app silent.

`est_amount` on every card is load-bearing, not decoration: `moved_on` can only
un-mute a card whose number has moved, so a card without one stays muted for the
whole TTL however far the position runs. That is exactly what happened to
`Trim: SOXL` after he marked it done on 2026-09-08.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.services.recommendations import _profit_recs  # noqa: E402


class _P:
    def __init__(self, ticker, quantity, price, cost, market="NASDAQ"):
        self.ticker, self.quantity = ticker, quantity
        self.current_price, self.cost_basis = price, cost
        self.market, self.meta = market, {}


# The live book on 2026-09-14. NAV 22,373.25.
NAV = 22373.25
SOXL = _P("SOXL", 22.7926, 121.82, 111.8837)
QQQ = _P("QQQ", 4.0215, 714.88, 718.96)
CASH = _P("CASH", 1193.21, 1.0, 1.0, market="TASE")
SNAP = {"nav": NAV, "exposure_ticker": {"SOXL": 0.378, "QQQ": 0.391,
                                        "CASH": 0.0533}}


def _rule(ticker, rule_type, level, target=None, active=True, **kw):
    return {"ticker": ticker, "rule_type": rule_type, "level": level,
            "target": target if target is not None else level,
            "active": active, **kw}


def test_it_warns_before_a_cap_you_set_is_breached():
    """SOXL at 37.8% against the 38% cap -- the live case, and the app's silence."""
    cards = _profit_recs([SOXL, CASH], SNAP,
                         [_rule("SOXL", "max_weight", 38.0)])
    near = [c for c in cards if c["id"].startswith("rec_") and "cap" in c["title"]]
    assert len(near) == 1, [c["title"] for c in cards]
    c = near[0]
    assert "37.8%" in c["action"] and "38% cap" in c["action"]
    assert "0.2 points of headroom" in c["action"]
    assert "going up" in c["action"], (
        "the point is that this breach arrives on a good day")


def test_a_far_away_take_profit_does_not_produce_a_card():
    """SOXL's is at +80% -- target 201.39 against 121.82. Nothing to say yet,
    and saying something anyway is how a card becomes noise."""
    cards = _profit_recs([SOXL], {"nav": NAV, "exposure_ticker": {"SOXL": 0.378}},
                         [_rule("SOXL", "take_profit", 80.0, target=201.39066)])
    assert [c for c in cards if "take-profit" in c["title"]] == []


def test_it_speaks_when_your_own_take_profit_is_close():
    """Within 10%: near enough to be a real decision, not tomorrow's firing."""
    cards = _profit_recs([SOXL], {"nav": NAV, "exposure_ticker": {"SOXL": 0.378}},
                         [_rule("SOXL", "take_profit", 130.0, target=130.0)])
    c = next(c for c in cards if "take-profit" in c["title"])
    assert c["severity"] == "MEDIUM"
    assert "6.7%" in c["action"], c["action"]
    # The slice it names must be the slice Accept would actually sell: 25% of
    # a 37.8% position on a 22,373 book.
    assert abs(c["est_amount"] - round(NAV * 0.378 * 0.25, 2)) < 0.01
    assert "25%" in c["action"], "the card must say how much it would sell"


def test_a_trailing_stop_card_says_the_whole_position_exits():
    """A trailing stop is a full exit; the card may not imply a trim."""
    cards = _profit_recs([SOXL], {"nav": NAV, "exposure_ticker": {"SOXL": 0.378}},
                         [_rule("SOXL", "trailing_stop", 20.0, target=115.0)])
    c = next(c for c in cards if "trailing stop" in c["title"])
    assert "whole position exits" in c["action"]
    assert abs(c["est_amount"] - round(NAV * 0.378, 2)) < 0.01


def test_a_big_gain_earns_a_card_even_with_no_rule_on_it():
    up = _P("NVDA", 10.0, 200.0, 100.0)
    cards = _profit_recs([up], {"nav": 2000.0, "exposure_ticker": {"NVDA": 1.0}}, [])
    c = next(c for c in cards if "+100% since you bought" in c["title"])
    assert "You have no take-profit on it." in c["action"]
    assert c["est_amount"] == 1000.0, "the unrealised gain, in shekels"


def test_a_small_gain_stays_quiet():
    """SOXL is up 8.9% -- real, and not something to act on."""
    cards = _profit_recs([SOXL], {"nav": NAV, "exposure_ticker": {"SOXL": 0.378}}, [])
    assert [c for c in cards if "is up" in c["title"]] == []


def test_every_card_carries_an_amount_so_the_mute_can_expire():
    """The defect that silenced `Trim: SOXL` for a fortnight."""
    cards = _profit_recs(
        [SOXL, QQQ, CASH], SNAP,
        [_rule("SOXL", "max_weight", 38.0),
         _rule("SOXL", "take_profit", 130.0, target=130.0),
         _rule("QQQ", "trailing_stop", 10.0, target=690.0)])
    assert cards, "fixture sanity"
    for c in cards:
        # `is not None` was the original assertion and it PASSED ON 0.0, which
        # is the same silence in a different costume: `moved_on` un-mutes on the
        # amount MOVING, and zero never moves. A check that cannot fail for the
        # thing it is checking is not a check.
        assert c.get("est_amount"), (
            f"{c['title']}: est_amount is {c.get('est_amount')!r} - a card with "
            f"no live amount can never un-mute, however far the position runs")
        assert float(c["est_amount"]) > 0, c["title"]


def test_cash_never_gets_a_profit_card():
    cards = _profit_recs([CASH], {"nav": NAV, "exposure_ticker": {"CASH": 1.0}},
                         [_rule("CASH", "take_profit", 1.05, target=1.05)])
    assert cards == []


def test_a_zero_quantity_ghost_never_gets_a_card():
    """USO at quantity 0 carried three armed rules on 2026-09-14. It must not
    also generate advice about selling something that is not there."""
    ghost = _P("USO", 0.0, 154.9, 141.96, market="NYSE")
    cards = _profit_recs([ghost], {"nav": NAV, "exposure_ticker": {"USO": 0.0}},
                         [_rule("USO", "take_profit", 168.17, target=168.17),
                          _rule("USO", "trailing_stop", 16.0, target=133.1064)])
    assert cards == []


def test_an_inactive_rule_is_not_a_level_you_hold():
    cards = _profit_recs([SOXL], {"nav": NAV, "exposure_ticker": {"SOXL": 0.378}},
                         [_rule("SOXL", "take_profit", 130.0, target=130.0,
                                active=False)])
    assert cards == []


# --------------------------------------------------------------------------- #
# PR 67.1 - found by running the smoke test against the live book an hour after
# PR 67 shipped. Both defects were in cards written to fix a silence problem,
# and both replaced it with a noise problem.
# --------------------------------------------------------------------------- #

def test_a_trailing_stop_at_its_peak_is_not_news():
    """A trailing stop RATCHETS. It sits at peak x (1 - trail), so a 10% trail
    is ~10% below the price by construction whenever the position is at its
    peak - and "within 10% of the level" is its resting state, not a warning.

    MEASURED on production 2026-09-14, the hour PR 67 shipped. Three cards
    fired at once:

        VTI   8.8% above its stop  ->  12.9% of the trail given back
        BND   8.7% above its stop  ->  14.2% of the trail given back
        QQQ   9.3% above its stop  ->   7.8% of the trail given back

    QQQ was seven per cent of the way from its peak to its stop and the app
    told him it was "closing on your trailing stop".
    """
    live = [
        ("VTI", 2.0505, 376.31, 378.335, 343.116, 10.0),
        ("BND", 7.4682, 71.22, 72.0754, 65.0205, 10.0),
        ("QQQ", 4.0215, 714.88, 718.96, 648.423, 10.0),
    ]
    for tk, qty, px, cost, target, trail in live:
        rows = [_P(tk, qty, px, cost)]
        snap = {"nav": NAV, "exposure_ticker": {tk: 0.10}}
        cards = _profit_recs(rows, snap,
                             [_rule(tk, "trailing_stop", trail, target=target)])
        assert cards == [], (
            f"{tk}: {(target / (1 - trail / 100) - px) / (target / (1 - trail / 100) - target):.1%} "
            f"of the trail given back is not 'closing on your trailing stop'")


def test_a_trailing_stop_that_has_really_been_given_back_does_speak():
    """Same rule, a position that has actually fallen most of the way."""
    # 20% trail, stop at 102.448 -> peak 128.06. At 108 the position has given
    # back (128.06 - 108) / (128.06 - 102.448) = 78% of its trail.
    rows = [_P("SOXL", 22.7926, 108.0, 111.8837)]
    snap = {"nav": NAV, "exposure_ticker": {"SOXL": 0.30}}
    cards = _profit_recs(rows, snap,
                         [_rule("SOXL", "trailing_stop", 20.0, target=102.448)])
    c = next(c for c in cards if "trailing stop" in c["title"])
    assert "given back" in c["title"]
    assert "78" in c["action"], c["action"]


def test_the_near_cap_card_carries_an_amount_that_actually_MOVES():
    """`est_amount` was the trim required at the cap, which is 0.0 by
    construction before the breach - so `moved_on` could never un-mute it,
    which is the exact defect these cards exist to fix.

    The original test asserted `est_amount is not None` and passed on 0.0: a
    check that could not fail for the thing it was checking.
    """
    def headroom_amount(weight):
        rows = [_P("SOXL", 22.7926, 121.82, 111.8837)]
        snap = {"nav": NAV, "exposure_ticker": {"SOXL": weight}}
        cards = _profit_recs(rows, snap, [_rule("SOXL", "max_weight", 38.0)])
        return next(c for c in cards if "cap" in c["title"])["est_amount"]

    a = headroom_amount(0.3779)   # 37.79% -> 0.21 points of headroom
    b = headroom_amount(0.3700)   # 37.00% -> 1.00 point
    assert a > 0, "an amount of zero can never un-mute"
    assert b > a, "the amount must move as the position does"
    assert abs(a - round(NAV * (38.0 - 37.79) / 100.0, 2)) < 0.5, a
