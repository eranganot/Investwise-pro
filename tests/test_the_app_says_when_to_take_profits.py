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
    c = next(c for c in cards if "up 100%" in c["title"])
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
        assert c.get("est_amount") is not None, (
            f"{c['title']}: a card with no amount can never un-mute, however "
            f"far the position runs")


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
