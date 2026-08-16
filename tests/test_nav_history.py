"""Phase N - a deposit is not performance.

The load-bearing test in this file is `test_a_deposit_does_not_move_the_return`.
Everything else is scaffolding around it. If time-weighting is wrong, the chart
is worse than the backfill it replaces: it carries the authority of real data
while being wrong in the direction that flatters.
"""
from __future__ import annotations

import pytest

from app.services import nav_history as nh


def _pts(*pairs):
    return [{"as_of": d, "nav_ils": v} for d, v in pairs]


# --------------------------------------------------------- the whole point
def test_a_deposit_does_not_move_the_return():
    """20,000 -> 25,000 because 5,000 was deposited is a 0% return, not +25%."""
    points = _pts(("2026-08-01", 20000.0), ("2026-08-02", 25000.0))
    flows = [{"occurred_at": "2026-08-02T09:00:00Z", "amount_ils": 5000.0}]

    twr = nh.time_weighted(points, flows)
    assert twr["total_pct"] == pytest.approx(0.0, abs=1e-6)

    # And the naive figure, reported beside it, is the one that would have lied.
    assert nh.simple_change_pct(points) == pytest.approx(25.0)


def test_a_withdrawal_does_not_manufacture_a_loss():
    points = _pts(("2026-08-01", 20000.0), ("2026-08-02", 15000.0))
    flows = [{"occurred_at": "2026-08-02T09:00:00Z", "amount_ils": -5000.0}]
    assert nh.time_weighted(points, flows)["total_pct"] == pytest.approx(0.0, abs=1e-6)
    assert nh.simple_change_pct(points) == pytest.approx(-25.0)


def test_a_deposit_plus_real_movement_reports_only_the_movement():
    # 20,000 -> grows 10% to 22,000 -> then 5,000 arrives = 27,000.
    points = _pts(("2026-08-01", 20000.0), ("2026-08-02", 27000.0))
    flows = [{"occurred_at": "2026-08-02T16:00:00Z", "amount_ils": 5000.0}]
    assert nh.time_weighted(points, flows)["total_pct"] == pytest.approx(10.0, abs=1e-6)


def test_returns_compound_across_periods():
    points = _pts(("2026-08-01", 100.0), ("2026-08-02", 110.0), ("2026-08-03", 121.0))
    assert nh.time_weighted(points, [])["total_pct"] == pytest.approx(21.0, abs=1e-6)


def test_the_first_point_is_a_baseline_not_a_return():
    twr = nh.time_weighted(_pts(("2026-08-01", 100.0), ("2026-08-02", 110.0)), [])
    assert twr["pct"][0] == 0.0
    assert twr["dates"][0] == "2026-08-01"


# ------------------------------------------------------------- flow window
def test_a_flow_on_the_opening_day_is_not_counted_twice():
    """It is already inside that snapshot's NAV. Counting it again would
    subtract money that was never added during the period."""
    assert nh.net_flow_between(
        [{"occurred_at": "2026-08-01T10:00:00Z", "amount_ils": 5000.0}],
        "2026-08-01", "2026-08-02") == 0.0


def test_a_flow_on_the_closing_day_is_counted():
    assert nh.net_flow_between(
        [{"occurred_at": "2026-08-02T10:00:00Z", "amount_ils": 5000.0}],
        "2026-08-01", "2026-08-02") == 5000.0


def test_flows_net_against_each_other():
    flows = [{"occurred_at": "2026-08-02", "amount_ils": 5000.0},
             {"occurred_at": "2026-08-02", "amount_ils": -1500.0}]
    assert nh.net_flow_between(flows, "2026-08-01", "2026-08-03") == 3500.0


def test_a_flow_with_no_date_is_ignored_rather_than_guessed_at():
    assert nh.net_flow_between([{"occurred_at": None, "amount_ils": 5000.0}],
                               "2026-08-01", "2026-08-09") == 0.0


# ------------------------------------------------------------------- edges
def test_a_zero_starting_value_carries_the_level_instead_of_dividing_by_zero():
    twr = nh.time_weighted(_pts(("2026-08-01", 0.0), ("2026-08-02", 100.0)), [])
    assert twr["total_pct"] == 0.0


def test_one_point_is_a_baseline_with_no_return():
    twr = nh.time_weighted(_pts(("2026-08-01", 100.0)), [])
    assert twr["pct"] == [0.0] and twr["total_pct"] == 0.0


def test_no_points_is_empty_not_zero_percent():
    twr = nh.time_weighted([], [])
    assert twr["points"] == 0 and twr["dates"] == []


# -------------------------------------------------------------------- gaps
def test_a_missing_stretch_is_reported_not_interpolated():
    """A straight line across a missing week claims nothing happened."""
    g = nh.gaps(_pts(("2026-08-01", 100.0), ("2026-08-12", 105.0)))
    assert len(g) == 1
    assert g[0]["days"] == 11 and g[0]["from"] == "2026-08-01"


def test_consecutive_days_and_a_weekend_are_not_gaps():
    assert nh.gaps(_pts(("2026-08-07", 100.0), ("2026-08-10", 101.0))) == []


def test_ranges_cover_every_button_the_card_offers():
    assert set(nh.RANGE_DAYS) == {"1W", "1M", "1Q", "1Y", "MAX"}
