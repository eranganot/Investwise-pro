"""A holding you entered is not a month you had.

Reported live, 2026-08-22: the account-history chart showed **+35%** on the day a
holding was added through the Holdings screen, under a caption reading

    "No deposits or withdrawals in this window, so this is simply what your
     money did."

Both halves false, and the second is the worse one -- a caption that vouches for
a number tells the reader the checking has already been done.

`time_weighted` takes its flows from the CONTRIBUTIONS LEDGER. Adding a holding
on the Holdings screen writes no ledger row, so `net_flow_between` returned 0.00
and the entire jump in NAV compounded as return.

The signal was already on the row and nothing read it. `NavSnapshot.positions`
is stored on every snapshot, and its own class docstring says why: *"a NAV that
jumped because money arrived is not a return, and without the deposit total on
the same row a later reader cannot tell the two apart."*
"""
import pytest

from app.services.nav_history import time_weighted


def _p(as_of, nav, positions, invested=0.0):
    return {"as_of": as_of, "nav_ils": nav, "positions": positions,
            "invested_ils": invested}


# The shape of the live incident: a flat book, then a holding is entered, then
# flat again. 10,000 -> 13,500 is the +35% that was reported as performance.
INTAKE_BOOK = [
    _p("2026-08-20", 10_000.0, 3),
    _p("2026-08-21", 10_000.0, 3),
    _p("2026-08-22", 13_500.0, 4),   # a 4th holding appears. No ledger row.
    _p("2026-08-23", 13_500.0, 4),
]


def test_a_holding_added_through_intake_is_not_performance():
    """The one that was live. No ledger row, so nothing else can catch it."""
    out = time_weighted(INTAKE_BOOK, flows=[])
    assert out["total_pct"] == pytest.approx(0.0, abs=0.01), (
        f"the chart reports {out['total_pct']}% for a book that earned nothing "
        "-- a holding was entered and the jump was booked as return")


def test_the_step_is_named_rather_than_folded_in_silently():
    """Attributing it is a judgement, so it has to be visible.

    The snapshot records a COUNT and a TOTAL -- never which holding arrived or
    what it was worth. Treating the delta as a flow is the same assumption the
    app already makes for a deposit, and it is still an assumption. Guessing
    quietly is how the +35% happened; guessing quietly in the other direction is
    not the fix.
    """
    out = time_weighted(INTAKE_BOOK, flows=[])
    flagged = out["unexplained_flows"]
    assert len(flagged) == 1, f"expected one named step, got {flagged}"
    assert flagged[0]["as_of"] == "2026-08-22"
    assert flagged[0]["amount_ils"] == pytest.approx(3500.0, abs=0.01)
    assert flagged[0]["positions_before"] == 3
    assert flagged[0]["positions_after"] == 4
    assert "not counted as performance" in flagged[0]["reason"]


def test_a_recorded_deposit_still_wins_over_the_position_count():
    """The ledger is authoritative when it has something to say.

    Both signals fire on the same day here. The ledger's figure must be the one
    used, or a deposit made on the same day a holding was entered would be
    counted twice.
    """
    out = time_weighted(INTAKE_BOOK,
                        flows=[{"occurred_at": "2026-08-22", "amount_ils": 3500.0}])
    assert out["total_pct"] == pytest.approx(0.0, abs=0.01)
    assert out["flow_total_ils"] == pytest.approx(3500.0, abs=0.01), (
        "the flow was counted twice -- once from the ledger and once from the "
        "position count")
    assert out["unexplained_flows"] == [], (
        "the ledger explained this step, so nothing is unexplained")


def test_real_performance_on_an_unchanged_book_is_untouched():
    """The guard must not eat the returns it exists to protect."""
    grew = [_p("2026-08-20", 10_000.0, 3),
            _p("2026-08-21", 10_500.0, 3),
            _p("2026-08-22", 11_025.0, 3)]
    out = time_weighted(grew, flows=[])
    assert out["total_pct"] == pytest.approx(10.25, abs=0.01)
    assert out["unexplained_flows"] == []


def test_a_holding_sold_out_of_is_not_a_loss():
    """The same arithmetic in reverse, and the same wrong answer without it."""
    sold = [_p("2026-08-20", 13_500.0, 4),
            _p("2026-08-21", 10_000.0, 3)]
    out = time_weighted(sold, flows=[])
    assert out["total_pct"] == pytest.approx(0.0, abs=0.01), (
        "removing a holding was reported as a loss")
    assert out["unexplained_flows"][0]["amount_ils"] == pytest.approx(-3500.0, abs=0.01)


def test_a_position_count_change_with_no_value_change_is_not_a_flow():
    """A swap moves no money and must not print a warning that says it did."""
    swapped = [_p("2026-08-20", 10_000.0, 3),
               _p("2026-08-21", 10_000.0, 4)]   # one holding split in two
    out = time_weighted(swapped, flows=[])
    assert out["unexplained_flows"] == []
    assert out["total_pct"] == pytest.approx(0.0, abs=0.01)
