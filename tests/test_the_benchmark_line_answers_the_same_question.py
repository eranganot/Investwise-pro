"""The SPY line must be measured over the same steps as the account line.

Eran, 2026-09-14: *"I want to add also the S&P line to see how is my portfolio
Vs S&P -- is it make sense or is too confusing?"*

It makes sense. It is also the single easiest thing in this app to get
convincingly wrong, because a benchmark series ALREADY EXISTS in
`performance()` and is aligned to a different portfolio.

Measured on production that day, same window (2026-08-17 -> 09-11):

    performance().portfolio_index      -8.28%   <- TODAY's book, priced backwards
    nav-history account line           +4.11%   <- what the account actually did
    SPY                                +0.18%

Drawing `performance().benchmark_index` on the account-history chart would put
SPY next to a series that is not the green line, and every reading taken off it
would be wrong by twelve points. That is the same shape as the two-targets-one-
book defect already in this repo's history, which is why `benchmark_series`
builds the line from the benchmark's own closes on the SNAPSHOT dates instead.
"""
from __future__ import annotations

from app.services import nav_history as nh


# SPY adjusted closes over Eran's live window, from the feed.
SPY = {
    "2026-08-14": 776.34, "2026-08-17": 772.67, "2026-08-18": 767.45,
    "2026-08-19": 769.06, "2026-08-20": 762.60, "2026-08-21": 765.72,
    "2026-08-24": 763.47, "2026-08-25": 765.91, "2026-08-26": 766.08,
    "2026-08-27": 771.10, "2026-08-28": 769.35, "2026-08-31": 767.05,
    "2026-09-01": 761.78, "2026-09-02": 765.16, "2026-09-03": 773.17,
    "2026-09-04": 770.19,
    # no 2026-09-07 -- US Labor Day, no session anywhere in the feed
    "2026-09-08": 765.96, "2026-09-09": 762.40, "2026-09-10": 757.83,
    "2026-09-11": 764.29,
}

# The account line exactly as `/portfolio/nav-history` returned it: cumulative
# percentages, with None on the five steps that carried no measurement.
DATES = ["2026-08-16", "2026-08-17", "2026-08-24", "2026-08-25", "2026-08-26",
         "2026-08-27", "2026-08-28", "2026-08-29", "2026-08-30", "2026-08-31",
         "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05",
         "2026-09-06", "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10",
         "2026-09-11", "2026-09-12", "2026-09-13"]
PCT = [0.0, 0.0, None, 0.7706, 0.8011, 3.486, 0.1281, None, 0.1281, 0.1687,
       -1.493, -0.6708, 0.5472, 3.753, 3.753, 3.638, None, None, 4.5934,
       1.2664, 4.1126, None, 4.1126]


def test_the_two_lines_have_the_same_shape():
    b = nh.benchmark_series(DATES, PCT, SPY)
    assert len(b["pct"]) == len(PCT) == len(DATES)
    assert b["pct"][0] == 0.0, "both lines start at the baseline, not at a return"


def test_the_benchmark_breaks_where_the_account_line_breaks():
    """A benchmark that keeps compounding across a step the account line
    refused to measure is comparing two different windows."""
    b = nh.benchmark_series(DATES, PCT, SPY)
    holes = [i for i, v in enumerate(PCT) if v is None]
    assert holes, "fixture sanity: the live window had five holes"
    for i in holes:
        assert b["pct"][i] is None, (
            f"{DATES[i]}: the account line has no measurement here, so the "
            f"benchmark may not claim one")


def test_a_shut_market_is_a_zero_for_the_index_not_a_hole():
    """2026-09-05 and 09-06 are a weekend the account line still measured
    (NAV was recorded, flat). SPY has no bar, and 0.0% is the truth for it."""
    b = nh.benchmark_series(DATES, PCT, SPY)
    i_fri, i_sat, i_sun = DATES.index("2026-09-04"), DATES.index("2026-09-05"), DATES.index("2026-09-06")
    assert b["pct"][i_sat] == b["pct"][i_fri] == b["pct"][i_sun], (
        "the index cannot move while the market is shut")


def test_it_reproduces_the_measured_comparison():
    """+0.18% for SPY against the account's +4.11%, over 17 measured steps."""
    b = nh.benchmark_series(DATES, PCT, SPY)
    assert abs(b["total_pct"] - 0.18) < 0.05, b["total_pct"]
    assert b["measured_steps"] == 17, b["measured_steps"]
    # The headline Eran actually wanted.
    assert round(PCT[-1] - b["total_pct"], 2) == 3.93


def test_it_does_not_use_the_performance_benchmark():
    """`performance()`'s benchmark is aligned to today's-book-priced-backwards.

    Pinned as source, because the wrong version of this is one import away and
    would look completely convincing on screen.
    """
    import inspect
    src = inspect.getsource(nh.series_for)
    assert "benchmark_series" in src
    assert "portfolio_index" not in src, (
        "the account-history chart must never borrow performance()'s series")


def test_no_benchmark_is_better_than_a_wrong_one():
    b = nh.benchmark_series(DATES, PCT, {})
    assert b["pct"] == [] and b["total_pct"] == 0.0
