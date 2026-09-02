"""PR 33 -- six of eleven recorded days were reported as exactly 0.00%.

`time_weighted` set the flow to the WHOLE inter-snapshot delta whenever the
position count changed and the ledger said nothing:

    f = nav - prev_nav
    r = (nav - f) / prev_nav - 1        ==  0.0, arithmetically, every time

So the one day the book changed shape is the one day the chart reports as
nothing happening -- and it reports it as a number, not as a gap.

MEASURED on Eran's book. On 2026-09-01 he deleted two holdings the app had
priced at 0 (PR 32 is why they were 0). NAV went 21,474.35 -> 20,777.45 and the
chart booked all 696.90 of it as "money you moved out", drawing the day at
0.00%. What actually happened, from real Yahoo closes 08-31 -> 09-01:

    SOXL   112.79 -> 105.91   -6.10%
    TQQQ    71.92 ->  69.15   -3.85%
    V      379.37 -> 372.67   -1.77%
    BND     72.24 ->  71.79   -0.62%

on a book that is 79% TQQQ and SOXL. The 696.90 was the market. Nothing left it.

THE FIX: do not guess, measure. `window_return` prices the book the user holds
today across the same two dates from real closes; what that explains is
performance, and only the residual is a flow:

    flow = nav - prev_nav x (1 + r_held)

Three outcomes, and each is said out loud rather than collapsed into a number:

  * residual is real            -> an ESTIMATED flow, labelled, with its method
  * residual is noise or wrong-signed  -> `resolved_changes`: the app checked,
    and the step was market movement. A removal cannot bring money IN, so a
    positive residual on a removal is the reconstruction's error term, not a
    deposit -- the recorded NAVs are the better evidence and the day is measured
    from them.
  * no price history at all     -> `unmeasured_days`: the line BREAKS. "We could
    not separate the book change from the market" is not a return of zero, and
    the chart may not assert one.

Nothing is recomputed into the database. `time_weighted` runs on every request,
so this repairs 24 and 29 August and 1 September the next time the page loads.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.services.nav_history import (  # noqa: E402
    time_weighted, window_return_from,
)

# Eran's real snapshot series, read from production on 2026-09-02.
# `positions` is what the row stores; the four count changes are the four days
# the old code reported as 0.00%.
BOOK = [
    {"as_of": "2026-08-16", "nav_ils": 21168.33, "positions": 10},
    {"as_of": "2026-08-17", "nav_ils": 21168.33, "positions": 10},
    {"as_of": "2026-08-24", "nav_ils": 21369.63, "positions": 5},
    {"as_of": "2026-08-25", "nav_ils": 21534.31, "positions": 5},
    {"as_of": "2026-08-26", "nav_ils": 21540.83, "positions": 5},
    {"as_of": "2026-08-27", "nav_ils": 22114.59, "positions": 5},
    {"as_of": "2026-08-28", "nav_ils": 21397.00, "positions": 5},
    {"as_of": "2026-08-29", "nav_ils": 21333.59, "positions": 4},
    {"as_of": "2026-08-30", "nav_ils": 21333.59, "positions": 4},
    {"as_of": "2026-08-31", "nav_ils": 21474.35, "positions": 6},
    {"as_of": "2026-09-01", "nav_ils": 20777.45, "positions": 5},
]

CHANGE_DAYS = ["2026-08-24", "2026-08-29", "2026-08-31", "2026-09-01"]


def _by_date(res):
    return dict(zip(res["dates"], res["pct"]))


# --------------------------------------------------------------------------- #
# what was there before
# --------------------------------------------------------------------------- #
def test_without_a_reconstruction_a_changed_day_is_a_hole_not_a_zero():
    """THE defect. Those four days must never be drawn as a flat 0.00% again."""
    res = time_weighted(BOOK, [])          # no window_return available
    pct = _by_date(res)

    for d in CHANGE_DAYS:
        assert pct[d] is None, (
            f"{d} came back as {pct[d]}%. The book changed shape and nothing "
            "could measure it, so the line must break rather than assert a "
            "return of zero.")
    named = {u["as_of"] for u in res["unmeasured_days"]}
    assert named == set(CHANGE_DAYS), f"unmeasured days not named: {named}"

    # and the days it CAN measure are untouched
    assert pct["2026-08-27"] is not None
    assert res["measured_steps"] == 6


def test_the_headline_says_how_much_of_the_window_it_covers():
    """A total compounded over 6 of 10 steps must not present as all ten."""
    res = time_weighted(BOOK, [])
    assert res["measured_steps"] == 6
    # the last MEASURED level, not the last slot -- the last slot is a hole
    assert res["total_pct"] is not None
    assert res["pct"][-1] is None


# --------------------------------------------------------------------------- #
# with the reconstruction -- the 2026-09-01 case, on the real numbers
# --------------------------------------------------------------------------- #
# The held book (TQQQ 38.7228, SOXL 26.0309, V 0.8314, BND 13.1331) valued at
# real closes. Base 100 on 28 Aug; the ratios are the real ones:
#   28 Aug basket 6960.35   31 Aug 6985.11   1 Sep 6687.28  (USD, from Yahoo)
# 30 Aug is a SUNDAY and has no bar, which is why the lookback matters: the
# snapshot on 30 Aug resolves back to Friday's close, and a book does not move
# while the market is shut.
HELD_INDEX = {"2026-08-28": 100.0, "2026-08-31": 100.3557, "2026-09-01": 96.0768}


def test_the_first_of_september_is_a_market_day_not_a_withdrawal():
    """696.90 ILS was the market. It must stop being reported as a withdrawal."""
    res = time_weighted(BOOK, [], window_return=window_return_from(HELD_INDEX))
    pct = _by_date(res)

    assert pct["2026-09-01"] is not None, "1 Sep is measurable and must be drawn"
    assert not any(u["as_of"] == "2026-09-01" for u in res["unexplained_flows"]), (
        "1 Sep is still being reported as money moving in or out")
    settled = {u["as_of"] for u in res["resolved_changes"]}
    assert "2026-09-01" in settled, (
        "the app must SAY it checked, not silently drop the warning")

    # nothing was charged to a flow on that day
    flow = dict(zip(res["dates"], res["flows_ils"]))["2026-09-01"]
    assert flow == pytest.approx(0.0, abs=0.5), (
        f"1 Sep still carries a {flow} ILS flow; the market took that money")

    # and the day's own step is the recorded NAV move: 20777.45/21474.35 - 1
    step = (1 + pct["2026-09-01"] / 100) / (1 + pct["2026-08-31"] / 100) - 1
    assert step == pytest.approx(-0.03245, abs=2e-4), (
        f"1 Sep booked at {step * 100:.3f}%; the recorded NAVs say -3.245%")


def test_a_removal_can_never_be_reported_as_money_arriving():
    """The residual on 1 Sep is +219 ILS on a day two holdings were REMOVED.

    That is the gap between the recorded NAV and a reconstruction built from
    today's holdings -- the error term this method carries -- not a deposit.
    Reading it as one would replace a false claim with another false claim.
    """
    res = time_weighted(BOOK, [], window_return=window_return_from(HELD_INDEX))
    for u in res["unexplained_flows"]:
        before, after = u["positions_before"], u["positions_after"]
        if after < before:
            assert u["amount_ils"] <= 0, (
                f"{u['as_of']}: {before} -> {after} holdings and the app says "
                f"{u['amount_ils']:+} ILS arrived")
        if after > before:
            assert u["amount_ils"] >= 0


def test_a_real_removal_is_still_attributed_as_a_flow():
    """The clamp must not swallow a genuine one.

    A holding worth a fifth of the book leaves on a flat market: the residual is
    large, correctly signed, and must be reported as a flow with its method.
    """
    pts = [
        {"as_of": "2026-08-31", "nav_ils": 10000.0, "positions": 5},
        {"as_of": "2026-09-01", "nav_ils": 8000.0, "positions": 4},
    ]
    res = time_weighted(pts, [], window_return=lambda a, b: 0.0)  # flat market
    assert len(res["unexplained_flows"]) == 1
    u = res["unexplained_flows"][0]
    assert u["amount_ils"] == pytest.approx(-2000.0, abs=1.0)
    assert u["estimated"] is True
    assert u["method"] == "measured_against_the_book_you_hold_today"
    # and the day is flat, because that is what the market did
    assert res["pct"][-1] == pytest.approx(0.0, abs=1e-6)


def test_the_ledger_still_wins_when_it_has_something_to_say():
    """A recorded deposit is a fact. The reconstruction must not second-guess it."""
    pts = [
        {"as_of": "2026-08-31", "nav_ils": 10000.0, "positions": 5},
        {"as_of": "2026-09-01", "nav_ils": 15000.0, "positions": 6},
    ]
    flows = [{"occurred_at": "2026-09-01", "amount_ils": 5000.0}]
    called = []

    def _spy(a, b):
        called.append((a, b))
        return -0.5

    res = time_weighted(pts, flows, window_return=_spy)
    assert called == [], "the ledger answered; nothing should have been reconstructed"
    assert res["pct"][-1] == pytest.approx(0.0, abs=1e-6)
    assert res["unexplained_flows"] == []


# --------------------------------------------------------------------------- #
# the helper
# --------------------------------------------------------------------------- #
def test_a_missing_date_is_not_a_zero_return():
    """None means "no measurement". Returning 0.0 here would reinstate the bug."""
    wr = window_return_from({"2026-08-31": 100.0, "2026-09-01": 95.7364})
    assert wr("2026-08-31", "2026-09-01") == pytest.approx(-0.042636, abs=1e-6)
    # before the series starts: nothing to reach back to
    assert wr("2026-08-25", "2026-09-01") is None
    # past the end by more than the lookback
    assert wr("2026-08-31", "2026-09-08") is None
    assert window_return_from({"2026-08-31": 0.0, "2026-09-01": 5.0})(
        "2026-08-31", "2026-09-01") is None


def test_a_weekend_snapshot_resolves_back_to_the_last_close():
    """2026-08-30 is a Sunday. A book does not move while the market is shut.

    Without this the reconstruction abstains on exactly the windows it is most
    obviously right about, and the day falls back to `unmeasured` for a reason
    that has nothing to do with the book.
    """
    wr = window_return_from({"2026-08-28": 100.0, "2026-08-31": 100.3557})
    assert wr("2026-08-30", "2026-08-31") == pytest.approx(0.003557, abs=1e-6)
    # but it does not reach across a week
    assert wr("2026-09-05", "2026-09-06") is None


def test_a_reconstruction_that_raises_degrades_to_unmeasured():
    """It must never degrade to zero, which is the defect wearing a fallback."""
    def _boom(a, b):
        raise RuntimeError("provider down")

    res = time_weighted(BOOK, [], window_return=_boom)
    pct = _by_date(res)
    for d in CHANGE_DAYS:
        assert pct[d] is None
    assert len(res["unmeasured_days"]) == len(CHANGE_DAYS)


def test_a_reconstruction_that_contradicts_the_book_is_a_hole_not_a_step():
    """Large AND wrong-signed means the method does not apply to this window.

    2026-08-24 on the real book is a reconcile that replaced half the holdings:
    pricing that window with the four tickers held today produces a 3,615 ILS
    residual -- 17% of NAV -- pointing the wrong way on a REMOVAL. Small and
    wrong-signed is the method's error term and the recorded NAVs win; large and
    wrong-signed is the method saying nothing useful, and the honest output is a
    hole rather than a confident step.
    """
    pts = [
        {"as_of": "2026-08-17", "nav_ils": 21168.33, "positions": 10},
        {"as_of": "2026-08-24", "nav_ils": 21369.63, "positions": 5},
    ]
    res = time_weighted(pts, [], window_return=lambda a, b: -0.1613)
    assert res["pct"][-1] is None, (
        f"booked at {res['pct'][-1]}% on a reconstruction that missed by 17% of NAV")
    assert [u["as_of"] for u in res["unmeasured_days"]] == ["2026-08-24"]
    assert res["resolved_changes"] == []

    # ...but a small contradiction still resolves to the recorded NAVs
    res2 = time_weighted(pts, [], window_return=lambda a, b: 0.0)
    assert res2["pct"][-1] == pytest.approx(0.951, abs=1e-2)
    assert [u["as_of"] for u in res2["resolved_changes"]] == ["2026-08-24"]


def test_a_stretched_window_is_refused_rather_than_measured_across_more_market():
    """`performance` intersects dates, so one missing bar drops the day for all.

    On this book 2026-08-28 is absent because V and BND had no close in the
    feed. Without the span guard, the 30 -> 31 Aug step would resolve back to
    27 Aug and price ONE day of NAV against FOUR days of market -- which
    invented a 1,088 ILS flow on a day nothing was deposited.
    """
    idx = {"2026-08-27": 100.0, "2026-08-31": 95.0}   # 28 and 30 Aug missing
    wr = window_return_from(idx)
    assert wr("2026-08-30", "2026-08-31") is None, (
        "a one-day step was priced across four days of market")
    # the same index answers a window it genuinely covers
    assert wr("2026-08-27", "2026-08-31") == pytest.approx(-0.05, abs=1e-9)
