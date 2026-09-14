"""A trade the ledger recorded is not money moving in or out of the account.

Eran, 2026-09-14: *"there are few holdings add/removed with no deposit/withdrawal
recorded."* He was right that nothing was deposited. The app was wrong to say
anything had been.

What the chart showed him on 2026-09-13:

    "Your value moved +5.69%, but -208.56 ILS of that was money you moved in or
     out."

Nothing moved in or out. Every book change in that window came from
`/recommendations/{id}/accept`, `/plan/target/apply`, `/plan/sleeves/fund`,
`/strategies/{id}/apply` and `/plan/sleeves/{id}/swap` -- internal reallocations,
every one. The -208.56 was the sum of two ESTIMATES (+132.11 on 08-31, -340.67 on
09-01) produced by pricing today's book backwards and calling the residual a
flow.

The estimate existed because `nav_history` had no way to tell a swap from a
deposit: it read only the CONTRIBUTIONS ledger, which correctly says nothing
about trades, and never asked the TRADE ledger, which exists for exactly this.
`/plan/sleeves/fund` compounded it by never writing to that ledger at all.
"""
from __future__ import annotations

from app.services import nav_history as nh


# THE REAL 2026-08-31 STEP, the one that produced the +132.11 on his screen.
# Two holdings were added, NAV moved 21,333.59 -> 21,474.35, and nothing entered
# or left the account. `held_book_return_pct` on that step was 0.0406%, so the
# residual is 21,474.35 - 21,333.59 x 1.000406 = +132.10 -- same-signed as the
# addition, under the 2% tolerance, and therefore reported as a flow.
POINTS = [
    {"as_of": "2026-08-30", "nav_ils": 21333.59, "positions": 4},
    {"as_of": "2026-08-31", "nav_ils": 21474.35, "positions": 6},
]
HELD_BOOK_RETURN = 0.000406


def _traded_on(*dates):
    s = set(dates)
    return lambda after, upto: any(after < d <= upto for d in s)


def test_without_the_ledger_the_app_invents_a_flow():
    """The behaviour being fixed, pinned so the fix cannot be mistaken for noise."""
    twr = nh.time_weighted(POINTS, [], window_return=lambda a, b: HELD_BOOK_RETURN)
    assert [round(f["amount_ils"], 2) for f in twr["unexplained_flows"]] == [132.1], (
        "baseline: with no ledger and a reconstruction available, the residual "
        "is reported as money moving")


def test_a_recorded_trade_explains_the_step_without_estimating():
    twr = nh.time_weighted(POINTS, [], traded=_traded_on("2026-08-31"),
                           window_return=lambda a, b: HELD_BOOK_RETURN)

    assert twr["unexplained_flows"] == [], (
        "a swap the ledger recorded must never be reported as a deposit or a "
        "withdrawal -- this is the -208.56 ILS caption")
    assert twr["flow_total_ils"] == 0.0
    assert twr["unmeasured_days"] == [], "the step is measurable, not a hole"

    resolved = {r["as_of"]: r for r in twr["resolved_changes"]}
    assert "2026-08-31" in resolved
    assert "not in or out of the account" in resolved["2026-08-31"]["reason"]


def test_the_step_books_the_market_move_from_the_recorded_navs():
    """21,474.35 / 21,333.59 - 1 = +0.66%. The market, not a transfer."""
    twr = nh.time_weighted(POINTS, [], traded=_traded_on("2026-08-31"),
                           window_return=lambda a, b: HELD_BOOK_RETURN)
    assert twr["pct"][-1] is not None, "must not be a hole"
    assert abs(twr["pct"][-1] - 0.6598) < 0.01, twr["pct"]


def test_a_real_deposit_is_still_a_deposit():
    """The ledger must not be able to launder an actual contribution away."""
    flows = [{"occurred_at": "2026-08-31", "amount_ils": 1000.0}]
    twr = nh.time_weighted(POINTS, flows, traded=_traded_on("2026-08-31"))
    assert twr["flow_total_ils"] == 1000.0
    assert twr["unexplained_flows"] == [], "it is explained -- it is in the ledger"
    # 21,474.35 - 1,000 = 20,474.35 against 21,333.59 -> -4.03%
    assert abs(twr["pct"][-1] - (-4.0276)) < 0.01, twr["pct"]


def test_a_trade_outside_the_window_does_not_explain_this_step():
    """Half-open, matching `net_flow_between`: a trade dated on the earlier
    snapshot is already inside that snapshot's NAV."""
    twr = nh.time_weighted(POINTS, [], traded=_traded_on("2026-08-30"),
                           window_return=lambda a, b: HELD_BOOK_RETURN)
    assert twr["unexplained_flows"], (
        "a trade on the earlier snapshot's own day cannot account for the step "
        "after it")


def test_the_sleeve_funding_path_records_its_legs():
    """`/plan/sleeves/fund` moved the book without writing one ledger row.

    `ledger_service`'s own docstring: *"if a write path can move a position and
    does not call record_leg, that path is a hole in the ledger."* This asserts
    the call exists, because the hole was invisible for the life of the app.
    """
    import inspect

    from app.services import strategy_service as ss

    src = inspect.getsource(ss._execute_funded_sleeve)
    assert "record_legs" in src, (
        "the sleeve funding path must record its buys and sells")
    assert src.index("record_legs") < src.rindex("session.commit"), (
        "the legs must be written in the same transaction as the book change, "
        "or the two can disagree")
