"""The chart's holes were weekends and Labor Day, not missing price history.

Measured on Eran's book on 2026-09-13: five of twenty-two steps carried no
measurement. Four were a calendar-cron snapshot landing on, or reaching across,
a day with no market session:

    2026-08-29 (Sat)  prev 08-28 (Fri) -> both ends resolve to the SAME close
    2026-09-12 (Sat)  prev 09-11 (Fri) -> both ends resolve to the SAME close
    2026-09-07 (US Labor Day, no bar) -> both ends resolve to 09-04
    2026-09-08 (Tue)  prev 09-07       -> resolved span 4 days against 1 asked

He read the caption -- "there is not enough price history" -- as the app having
lost his records. Every record was there.

Two things are pinned here: the snapshot job does not create those points in the
first place, and when an unmeasurable step does occur the caption names the
market being shut rather than blaming the price history.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services import nav_history as nh


# The real 2026 index around Labor Day: no bar on Monday 09-07.
SESSIONS = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
            "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"]
INDEX = {d: 100.0 + i for i, d in enumerate(SESSIONS)}


def test_a_saturday_is_not_a_trading_day_and_needs_no_feed(monkeypatch):
    """Weekends are decided without touching the provider -- no failure mode."""
    def _boom(*a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("the provider must not be consulted for a weekend")
    monkeypatch.setattr("app.providers.registry.guarded_history", _boom)

    trading, why = nh.is_trading_day(date(2026, 9, 12))   # Saturday
    assert trading is False
    assert "Saturday" in why

    trading, why = nh.is_trading_day(date(2026, 9, 13))   # Sunday
    assert trading is False
    assert "Sunday" in why


def test_labor_day_is_not_a_trading_day(monkeypatch):
    """A weekday the feed has no close for, after the close has landed."""
    monkeypatch.setattr(nh, "_CLOSE_LANDED_UTC_HOUR", 0)  # "the close has landed"
    monkeypatch.setattr("app.providers.registry.guarded_history",
                        lambda *a, **k: [("2026-09-04", 100.0)])
    trading, why = nh.is_trading_day(date(2026, 9, 7))    # Monday, Labor Day
    assert trading is False
    assert "2026-09-07" in why and "2026-09-04" in why


def test_an_ordinary_weekday_with_a_close_records():
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(nh, "_CLOSE_LANDED_UTC_HOUR", 0)
        monkeypatch.setattr("app.providers.registry.guarded_history",
                            lambda *a, **k: [("2026-09-11", 100.0)])
        trading, _ = nh.is_trading_day(date(2026, 9, 11))   # Friday
        assert trading is True
    finally:
        monkeypatch.undo()


@pytest.mark.parametrize("series,label", [
    (None, "provider raises"),
    ([], "provider returns nothing"),
])
def test_it_fails_open_when_the_market_cannot_be_asked(monkeypatch, series, label):
    """A snapshot NOT taken is permanent data loss; one taken on a dead day is
    a single unmeasured step. The asymmetry decides every uncertain case."""
    monkeypatch.setattr(nh, "_CLOSE_LANDED_UTC_HOUR", 0)

    def _provider(*a, **k):
        if series is None:
            raise RuntimeError("provider down")
        return series
    monkeypatch.setattr("app.providers.registry.guarded_history", _provider)

    trading, why = nh.is_trading_day(date(2026, 9, 9))   # an ordinary Wednesday
    assert trading is True, f"{label}: must record rather than lose the day"
    assert "recording rather than guessing" in why


def test_an_early_run_records_rather_than_guessing(monkeypatch):
    """Before the close lands, a missing bar means 'not yet', not 'no market'.
    Without this, moving the cron earlier would silently stop all history."""
    monkeypatch.setattr(nh, "_CLOSE_LANDED_UTC_HOUR", 99)   # always "too early"
    monkeypatch.setattr("app.providers.registry.guarded_history",
                        lambda *a, **k: [("2026-01-01", 100.0)])
    trading, why = nh.is_trading_day(date(2026, 9, 9))
    assert trading is True
    assert "before the close has landed" in why


def test_the_caption_blames_the_market_not_the_price_history():
    """The exact 09-07 step, with the exact index that produced the complaint."""
    reason = nh.window_reason_from(INDEX)

    # 2026-09-06 (Sun) -> 2026-09-07 (Labor Day): no session in between at all.
    why = reason("2026-09-06", "2026-09-07")
    assert "market was shut" in why
    assert "price history" not in why, (
        "this is the sentence Eran read as the app losing his records")

    # 2026-09-11 (Fri) -> 2026-09-12 (Sat): a weekend, named as one.
    assert "weekend" in reason("2026-09-11", "2026-09-12")

    # 2026-09-07 -> 2026-09-08 does contain a session, but the step has to reach
    # across the holiday to find its other end.
    assert "shut for part of this step" in reason("2026-09-07", "2026-09-08")


def test_the_unmeasured_day_carries_the_real_reason():
    """End to end through `time_weighted`, on a holiday-crossing shape change."""
    points = [
        {"as_of": "2026-09-04", "nav_ils": 21883.94, "positions": 5},
        {"as_of": "2026-09-06", "nav_ils": 21859.69, "positions": 5},
        {"as_of": "2026-09-07", "nav_ils": 21881.22, "positions": 6},
    ]
    twr = nh.time_weighted(
        points, [],
        window_return=nh.window_return_from(INDEX),
        window_reason=nh.window_reason_from(INDEX))

    days = {d["as_of"]: d for d in twr["unmeasured_days"]}
    assert "2026-09-07" in days, "the holiday step is still unmeasurable"
    reason = days["2026-09-07"]["reason"]
    assert "market was shut" in reason
    assert "not enough price history" not in reason
