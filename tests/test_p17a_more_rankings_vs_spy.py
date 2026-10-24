"""P17a (owner, 2026-10-24: "Test other rankings first"). Quality Momentum 5 measured 13.8%/yr
against QQQ's 20.3% on production (10 years to 2026-10-23) and lowered the book's return at
15% (24.7 -> 24.3%/yr). Before building live stock rotation, three more rankings are
pre-registered and measured: 30-day momentum, and the 12-month rankings with a 30-day filter;
each is held against SPY too, and the book effect can be read at 15, 25 or 30%.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.engines import strategy_backtest as bt  # noqa: E402
from app.services import round2 as r2  # noqa: E402
from tests.test_pr134b_round2 import _book, _fetch_from, _series  # noqa: E402


def test_the_three_rankings_are_registered_as_written():
    r1, r2b, r3 = r2.spec("R1"), r2.spec("R2"), r2.spec("R3")
    assert (r1["overlay"]["score"], r1["overlay"]["lookback_days"], r1["overlay"]["top_n"]) == ("ret", 21, 10)
    assert (r2b["overlay"]["score"], r2b["overlay"]["recent_filter_days"], r2b["overlay"]["top_n"]) == ("mom_12_1", 21, 10)
    assert (r3["overlay"]["score"], r3["overlay"]["recent_filter_days"], r3["overlay"]["top_n"]) == ("risk_adj_mom", 21, 5)
    for s in (r1, r2b, r3):
        assert s["base"] == {"BIL": 1.0} and s["overlay"]["market_gate"]["ticker"] == "SPY"
        assert s["overlay"]["universe"] == r2.US115 and s["overlay"]["cadence_days"] == 21
    assert r2.spec("S2")["overlay"].get("recent_filter_days") is None, "the original S2 is unchanged"


def test_the_30_day_filter_drops_a_name_that_fell_over_the_last_month():
    n = 400
    up = list(np.full(n, 0.002))
    fell = list(np.full(n - 21, 0.004)) + list(np.full(21, -0.01))   # best 12 months, down this month
    series = {"UP": _series(up), "FELL": _series(fell), "BIL": _series(np.zeros(n)), "SPY": _series(up)}
    spec = {"id": "x", "weights": {}, "base": {"BIL": 1.0},
            "overlay": {"kind": "rank_select", "universe": ["UP", "FELL"], "score": "mom_12_1", "top_n": 1,
                        "buffer": 1, "cadence_days": 21}}
    px = bt.align(series)[1]
    plain = bt._rank_select(px, spec)
    filt = bt._rank_select(px, {**spec, "overlay": {**spec["overlay"], "recent_filter_days": 21}})
    assert "FELL" in plain[-1] and "FELL" not in filt[-1] and "UP" in filt[-1], (plain[-1], filt[-1])


@pytest.mark.parametrize("code", ["R1", "R2", "R3"])
def test_each_ranking_runs_and_is_held_against_spy(code):
    out = r2.evaluate_stock_blocking(code, _book(), ratio_gain=0.02, drop_tol=2.0, fetch=_fetch_from({}),
                                     sleeve_pct=25.0)
    assert out["ok"], out
    assert out["sleeve_pct"] == 25.0
    vs = out["tests"]["vs_spy"]
    assert vs["spy_cagr_pct"] is not None and vs["pass"] == (vs["cagr_pct"] > vs["spy_cagr_pct"])
    assert out["passes_before_s5"] == all(t["pass"] for k, t in out["tests"].items() if k != "vs_spy")


def test_the_size_is_15_25_or_30():
    with TestClient(m.app) as c:
        assert c.get("/api/v1/strategies/round2/stocks/R1/measure?sleeve_pct=40").status_code == 422
