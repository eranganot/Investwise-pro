"""T0.1 / T0.2 / T0.4 — two benchmarks, provenance on every figure, staleness.

T0.1  A strategy measured against SPY is mostly being measured on a Nasdaq bet.
      The catalog already names the right comparison in `base`; these tests
      assert it is reported as its OWN field and cannot be confused with the
      book benchmark.
T0.2  Every figure carries the window it came from, and what kind of
      measurement it is, so a ten-year backtest and a 250-day backfill can share
      a screen without contradicting each other.
T0.4  An excess is only meaningful against the benchmark it was measured
      against, so a changed setting invalidates the row.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from app.engines import strategy_backtest as bt
from app.models.tables import StrategyBacktest
from app.services import backtest_service as bs

DATES = [f"2026-01-{d:02d}" for d in range(1, 21)]


def _ramp(start: float, end: float, n: int = len(DATES)) -> np.ndarray:
    return np.linspace(start, end, n)


# --------------------------------------------------------------- T0.1
def test_base_series_is_buy_and_hold_of_the_thing_being_levered():
    px = {"QQQ": _ramp(100.0, 120.0)}
    s = bt.base_series(px, {"QQQ": 1.0})
    assert s is not None
    assert s[0] == pytest.approx(1.0)
    assert s[-1] == pytest.approx(1.2)


def test_base_series_weights_a_multi_leg_base():
    px = {"A": _ramp(100.0, 200.0), "B": _ramp(100.0, 100.0)}
    s = bt.base_series(px, {"A": 0.5, "B": 0.5})
    assert s[-1] == pytest.approx(1.5)   # 0.5*2.0 + 0.5*1.0


def test_base_series_abstains_rather_than_guessing():
    assert bt.base_series({"QQQ": _ramp(1, 2)}, None) is None
    assert bt.base_series({"QQQ": _ramp(1, 2)}, {"SMH": 1.0}) is None


def test_base_and_benchmark_excess_are_separate_fields():
    """The rule can beat what it levers while the book still loses to SPY.
    Two questions, two fields; collapsing them is how that goes unnoticed."""
    out = {"cagr_pct": 12.0}
    px = {"QQQ": _ramp(100.0, 100.0)}          # base flat -> excess over base = 12
    bt._base_metrics(out, px, {"base": {"QQQ": 1.0}})

    assert out["base_tickers"] == ["QQQ"]
    assert out["excess_over_base_cagr_pct"] == pytest.approx(12.0, abs=0.5)
    # The book-benchmark keys are produced elsewhere (_metrics) and must not be
    # written by the base pass at all.
    assert "excess_cagr_pct" not in out
    assert "benchmark_cagr_pct" not in out


def test_no_base_reports_absence_rather_than_zero():
    out = {"cagr_pct": 12.0}
    bt._base_metrics(out, {}, {"weights": {"MTUM": 1.0}})   # factor stack: no base
    assert out["base_tickers"] is None
    assert "excess_over_base_cagr_pct" not in out


# --------------------------------------------------------------- T0.2 / T0.4
def _row(**kw) -> StrategyBacktest:
    row = StrategyBacktest(strategy_id="btm_trend_tqqq")
    row.engine_version = bs.ENGINE_VERSION
    row.ok = True
    row.metrics = {"cagr_pct": 10.0}
    row.robustness = {}
    row.reason = ""
    row.detail = ""
    row.last_error = ""
    row.last_error_at = None
    row.data_source = "yahoo"
    row.period_start = "2016-01-04"
    row.period_end = "2026-08-14"
    row.observations = 2530
    row.benchmark_ticker = "SPY"
    row.computed_at = datetime.now(timezone.utc)
    for k, v in kw.items():
        setattr(row, k, v)
    return row


def test_payload_carries_its_window_and_kind():
    p = bs._payload(_row())
    assert p["window"] == {"start": "2016-01-04", "end": "2026-08-14",
                           "sessions": 2530, "kind": "strategy_backtest"}
    assert p["benchmark_ticker"] == "SPY"


def test_a_changed_benchmark_marks_the_row_stale():
    row = _row(benchmark_ticker="QQQ")     # measured against something else
    assert bs._is_stale(row) is True
    assert bs._payload(row)["stale_reason"] == "benchmark"


def test_a_matching_benchmark_is_not_stale():
    row = _row(benchmark_ticker=bs.get_settings().benchmark_ticker)
    assert bs._is_stale(row) is False
    assert bs._payload(row)["stale_reason"] is None


def test_an_empty_benchmark_is_not_treated_as_a_mismatch():
    """Rows predating the column are stale by engine_version, not by benchmark.
    Guessing one for them would invent the fact the column exists to record."""
    row = _row(benchmark_ticker="")
    assert bs._benchmark_changed(row) is False
