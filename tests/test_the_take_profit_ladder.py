"""A take-profit sells a slice and comes back higher, instead of liquidating once.

Eran, 2026-09-14, on why the app never tells him to take money off the table:
*"I'm holding assets that are moving which is great but I don't see any card
recommendation about when to sell to trim profits."*

Three separate things were true on his book that day, and this file pins the
first. `take_profit` fired as `sell_position` -- the whole position -- while the
note the app writes on its own take-profit rules says "Takes some off the table
if it climbs ~10% more" (live on QQQ at 791.02 and USO at 168.17). And because
`take_profit` sat in `_ONE_SHOT`, the rule then switched itself off forever.

WHY NOT A TREND-CONDITIONED SLICE, which is what he asked about. STATUS.md B2
measures that exact idea on this book over 2,514 sessions: the trend overlay on
TQQQ returned 17.19% CAGR against 40.25% for simply holding it and 20.73% for
holding QQQ, with a DEEPER drawdown (40.55% vs 35.12%) and ~7 points a year lost
to 45 round-trips a year. The trend-dependent exit already exists and is
correct -- `trailing_stop` ratchets off `peak_price`, so it sells late in a
strong trend and early in a weak one without forecasting anything.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.services import rules_service as rs  # noqa: E402


def test_the_ladder_step_moves_a_price_level_up_by_ten_percent():
    # QQQ's live take-profit: 791.02 -> 870.12.
    assert rs.next_rung(791.02, "price") == 870.122


def test_the_ladder_step_moves_a_pct_level_by_points_not_by_ratio():
    """SOXL's take-profit is `pct` mode at 80 -- a GAIN of 80% over cost.

    The next rung is 90% over cost, not 88%. Compounding a percentage-of-cost
    level as though it were a price is how a ladder quietly stops being evenly
    spaced.
    """
    assert rs.next_rung(80.0, "pct") == 90.0


def test_a_fired_take_profit_rearms_higher_instead_of_switching_off():
    """The behaviour `_ONE_SHOT` gave it: fire once, then never again."""
    assert "take_profit" in rs._ONE_SHOT, (
        "still listed, because a DECLINED take-profit must still latch off")

    import inspect
    src = inspect.getsource(rs.resolve_rule)
    assert 'rule.rule_type == "take_profit"' in src
    assert "next_rung" in src
    assert 'str(outcome) == "accepted"' in src, (
        "only an ACCEPTED firing may move the level -- re-asking a question the "
        "user declined, from a higher level, is nagging")


def test_the_slice_and_the_rung_are_consistent_over_four_firings():
    """A quarter each time, laddering up, leaves the winner running.

    Four rungs from QQQ's live 791.02, on a 4.0215-share position, selling a
    quarter of what REMAINS each time -- so the position is never exhausted and
    the trailing stop always has something left to protect.
    """
    level, qty = 791.02, 4.0215
    sold = 0.0
    for _ in range(4):
        slice_qty = qty * rs.TAKE_PROFIT_FRACTION
        sold += slice_qty
        qty -= slice_qty
        level = rs.next_rung(level, "price")

    assert round(level, 2) == 1158.13, "four rungs of +10% (791.02 x 1.1^4)"
    assert round(qty, 4) == 1.2724, "a quarter of the remainder, four times"
    assert qty > 0, "a ladder must never liquidate the position it is laddering"
    assert round(sold + qty, 4) == 4.0215, "no shares invented or lost"


def test_a_declined_take_profit_still_latches_off():
    """Unchanged behaviour, asserted so the ladder cannot quietly widen."""
    import inspect
    src = inspect.getsource(rs.resolve_rule)
    accepted_at = src.index('str(outcome) == "accepted"')
    elif_at = src.index("elif rule.rule_type in _ONE_SHOT")
    assert accepted_at < elif_at, (
        "anything that is not an accepted take-profit must fall through to the "
        "one-shot branch")
