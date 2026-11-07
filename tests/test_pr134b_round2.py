"""PR 134b: round 2 (docs/plans/ROUND2_STOCKS_AND_SLEEVE_FUNDING_2026-10-15.md).

Owner, 2026-10-15: "once you complete 134a I want you to run both option 2 and option 3"; option 2 approved as
written; option 3 at the suggested size AND at double ("both").

Not financial advice.
"""
import zlib
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from datetime import date, timedelta  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.engines import strategy_backtest as bt  # noqa: E402
from app.services import book_effect as be  # noqa: E402
from app.services import round2 as r2  # noqa: E402
from app.services import strategy_catalog as cat  # noqa: E402


def _series(returns, start=100.0):
    px, out, d0 = start, [], date(2016, 1, 4)
    for i, r in enumerate(returns):
        px *= 1 + r
        out.append(((d0 + timedelta(days=i)).isoformat(), px))
    return out


def _walk(seed, n=900, drift=0.0003, vol=0.01):
    return np.random.default_rng(seed).normal(drift, vol, n)


def _fetch_from(paths):
    def fetch(tickers, **kw):
        return {t: _series(paths[t] if t in paths else _walk(zlib.crc32(t.encode()) % 1000)) for t in tickers}, []
    return fetch


# --------------------------------------------------------------------------- #
# option 2: the approved specs
# --------------------------------------------------------------------------- #
def test_us115_is_the_approved_list_once_each():
    assert len(r2.US115) == 115 == len(set(r2.US115))
    assert {"AAPL", "BRK-B", "LLY", "GEV", "AMT"} <= set(r2.US115)


def test_s1_to_s3_are_what_the_owner_approved():
    s1, s2, s3 = r2.spec("S1"), r2.spec("S2"), r2.spec("S3")
    for s in (s1, s2, s3):
        o = s["overlay"]
        assert s["base"] == {"BIL": 1.0}, "T-bills when the market gate is off, not QQQ"
        assert o["market_gate"] == {"ticker": "SPY", "ma_days": 200, "confirm_days": 3}
        assert o["universe"] == r2.US115 and o["cadence_days"] == 21
    assert (s1["overlay"]["score"], s1["overlay"]["top_n"], s1["overlay"]["buffer"]) == ("mom_12_1", 10, 20)
    assert (s2["overlay"]["score"], s2["overlay"]["top_n"], s2["overlay"]["buffer"]) == ("risk_adj_mom", 5, 10)
    assert (s3["overlay"]["score"], s3["overlay"]["top_n"], s3["overlay"]["buffer"]) == ("high_52w", 6, 12)
    a = np.linspace(100, 200, 400)
    for s in (s1, s2, s3):
        assert bt.score_at(a, 399, s["overlay"]["score"], s["overlay"]) is not None


@pytest.mark.parametrize("code", ["S1", "S2", "S3"])
def test_each_s_candidate_runs_on_the_engine(code):
    s = r2.spec(code)
    series, _ = _fetch_from({})(bt.tickers_needed(s))
    r = bt.run(series, s)
    assert r.get("ok"), (code, r.get("reason"), r.get("detail"))
    assert r.get("selection_edge_pct") is not None


# --------------------------------------------------------------------------- #
# option 3: funded from a named sleeve
# --------------------------------------------------------------------------- #
def _book():
    vol = cat._spec(cat.get(r2.LEVERAGED_SLEEVE))
    return {"floor": 0.01, "bench_tk": "SPY",
            "core": {"id": "core", "weights": {"VTI": 0.6, "BND": 0.4}, "overlay": {"kind": "buy_hold"}},
            "sleeves": [{"id": r2.LEVERAGED_SLEEVE, "spec": vol, "current_pct": 40.0}]}


def test_fund_from_shrinks_that_sleeve_and_leaves_the_core():
    calls = []
    real = be.blend.measure_blend

    def spy(series, comps, **kw):
        calls.append({c["id"]: round(c["weight"], 4) for c in comps})
        return real(series, comps, **kw)

    be.blend.measure_blend, orig = spy, be.blend.measure_blend
    try:
        cand = {"id": "gold", "weights": {"GLD": 1.0}, "overlay": {"kind": "buy_hold"}}
        r = be.compare_blocking(_book(), cand, 10, _fetch_from({}), fund_from=r2.LEVERAGED_SLEEVE)
    finally:
        be.blend.measure_blend = orig
    assert r["ok"] and r["funded_from"] == r2.LEVERAGED_SLEEVE
    without, with_ = calls
    assert without[r2.LEVERAGED_SLEEVE] == 0.40 and with_[r2.LEVERAGED_SLEEVE] == 0.30
    assert without["core"] == with_["core"], "the core must not make the room"
    assert with_["gold"] == 0.10


def test_a_sleeve_not_held_cannot_make_the_room():
    cand = {"id": "gold", "weights": {"GLD": 1.0}, "overlay": {"kind": "buy_hold"}}
    r = be.compare_blocking(_book(), cand, 10, _fetch_from({}), fund_from="btm_trend_soxl")
    assert r["ok"] is False and r["reason"] == "NOT_HELD"


def test_option_3_reports_f1_against_qqq_and_f2_and_doubles():
    one = r2.evaluate_funding_blocking("D3", _book(), scale=1, ratio_gain=0.02, drop_tol=2.0,
                                       fetch=_fetch_from({}))
    two = r2.evaluate_funding_blocking("D3", _book(), scale=2, ratio_gain=0.02, drop_tol=2.0,
                                       fetch=_fetch_from({}))
    assert one["ok"] and two["ok"]
    assert one["moved_pct"] == 10.0 and two["moved_pct"] == 20.0
    assert one["qqq_cagr_pct"] is not None
    assert set(one["tests"]) == {"F1", "F2"}
    assert one["passes"] == (one["tests"]["F1"]["pass"] and one["tests"]["F2"]["pass"])


def test_option_3_takes_s_candidates_too():
    r = r2.evaluate_funding_blocking("S1", _book(), scale=1, ratio_gain=0.02, drop_tol=2.0, fetch=_fetch_from({}))
    assert r["ok"] and r["moved_pct"] == 20.0


def test_unknown_candidates_are_refused():
    assert r2.evaluate_funding_blocking("X9", _book(), scale=1, ratio_gain=0.02, drop_tol=2.0)["ok"] is False
    assert r2.evaluate_stock_blocking("D3", None, ratio_gain=0.02, drop_tol=2.0)["ok"] is False


def test_scale_is_one_or_two_as_approved():
    with TestClient(m.app) as c:
        assert c.get("/api/v1/strategies/round2/funding/D3/measure?scale=3").status_code == 422
