"""PR 133: wave 3 -- different strategies that beat the market, measured against the
owner-approved gate (docs/plans/DIVERSIFIERS_2026-10-15.md), and the book-effect
verdict that says which test failed.

Owner, 2026-10-15: "the main goal is not only to diversify but to beat the market
(diversification is just a plus)"; "yes, add them, but I want to see the risk level
per strategy"; on "does not improve your book": "is that suggestion that no one of
the sleeves are good to be taken?".

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pathlib  # noqa: E402
from datetime import date, timedelta  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.engines import strategy_backtest as bt  # noqa: E402
from app.services import book_effect as be  # noqa: E402
from app.services import diversifier_candidates as dc  # noqa: E402
from app.services import diversifier_gate as gate  # noqa: E402


def _series(returns, start=100.0):
    px, out, d0 = start, [], date(2016, 1, 4)
    for i, r in enumerate(returns):
        px *= 1 + r
        out.append(((d0 + timedelta(days=i)).isoformat(), px))
    return out


def _walk(seed, n=900, drift=0.0003, vol=0.01):
    return np.random.default_rng(seed).normal(drift, vol, n)


def _fetch_from(paths):
    def fetch(tickers):
        out = {t: _series(paths[t] if t in paths else _walk(abs(hash(t)) % 1000)) for t in tickers}
        return out, []
    return fetch


# --------------------------------------------------------------------------- #
# the candidates are exactly the approved list
# --------------------------------------------------------------------------- #
def test_thirteen_candidates_d1_to_d13_each_with_a_risk_level():
    assert [c["code"] for c in dc.CANDIDATES] == [f"D{i}" for i in range(1, 14)]
    for c in dc.CANDIDATES:
        assert c["expected_risk"] and c["sleeve_pct"] > 0, c["code"]
    assert {c["code"] for c in dc.CANDIDATES if c["family"] == "D"} == {"D11", "D12", "D13"}
    assert {c["code"] for c in dc.CANDIDATES if c.get("short_history")} == {"D7", "D8"}


def test_every_candidate_runs_on_the_engine():
    fetch = _fetch_from({})
    for c in dc.CANDIDATES:
        spec = dc.spec(c["id"])
        series, _ = fetch(bt.tickers_needed(spec))
        r = bt.run(series, spec)
        assert r.get("ok"), (c["code"], r.get("reason"), r.get("detail"))


def test_specs_are_what_the_owner_approved():
    assert dc.spec("D1")["overlay"]["universe"] == ["SPY", "EFA", "IEF", "DBC", "VNQ"]
    assert dc.spec("D1")["base"] == {"BIL": 1.0} and dc.spec("D1")["overlay"]["trend_veto_days"] == 200
    assert dc.spec("D9")["base"] == {"PSQ": 1.0} and dc.spec("D10")["base"] == {"SH": 1.0}
    assert dc.spec("D11")["overlay"]["legs"] == {"SSO": "SPY", "UBT": "TLT", "UGL": "GLD"}
    assert dc.spec("D13")["weights"] == {"TMF": 1.0} and dc.spec("D13")["overlay"]["gate_ticker"] == "TLT"
    assert dc.spec("D7")["long_history"] is False and dc.spec("D3")["long_history"] is True


# --------------------------------------------------------------------------- #
# rebuilt leveraged and inverse funds
# --------------------------------------------------------------------------- #
def test_a_minus_one_fund_falls_when_its_index_rises_and_earns_the_rate_when_flat():
    up = [("2010-01-0%d" % (i + 1), 100.0 * 1.01 ** i) for i in range(5)]
    flat = [("2010-01-0%d" % (i + 1), 100.0) for i in range(5)]
    rate = [(d, 4.0) for d, _ in up]
    inv_up = bt.synthetic_leveraged(up, -1, rate)
    inv_flat = bt.synthetic_leveraged(flat, -1, rate)
    assert inv_up[-1][1] < 97.0
    # Flat index: earns ~(1 - (-1)) x 4% less spread and fee, a little each day.
    assert inv_flat[-1][1] > 100.0
    for tk in ("SSO", "UBT", "UGL", "PSQ", "SH", "TMF"):
        assert tk in bt.SYNTHETIC


# --------------------------------------------------------------------------- #
# the book-effect verdict says which test failed; its limits are settings
# --------------------------------------------------------------------------- #
def test_the_verdict_names_the_failed_test_and_its_margin():
    v = be.verdict(0.05, 9.1, 0.02, 2.0)
    assert v["improves"] is False and [f["test"] for f in v["failed"]] == ["drop"]
    assert "9.1 points deeper; your limit is 2" in v["failed"][0]["text"]
    v = be.verdict(-0.004, 1.5, 0.02, 2.0)
    assert [f["test"] for f in v["failed"]] == ["ratio"] and "-0.004" in v["failed"][0]["text"]
    assert be.verdict(0.03, 1.0, 0.02, 2.0) == {"improves": True, "failed": []}


def test_the_two_limits_are_settings():
    from app.services import thresholds
    assert thresholds.defaults()["book_effect_ratio_gain"] == 0.02
    assert thresholds.defaults()["book_effect_drop_tolerance_pts"] == 2.0
    tok = thresholds._OVERRIDES.set({"book_effect_drop_tolerance_pts": 10.0})
    try:
        rg, dt = be.limits()
        assert dt == 10.0 and be.verdict(0.05, 9.1, rg, dt)["improves"] is True
    finally:
        thresholds._OVERRIDES.reset(tok)


def test_the_strategy_card_line_shows_why():
    html = (pathlib.Path(__file__).resolve().parents[1] / "app" / "static_app" / "index.html").read_text(encoding="utf-8")
    i = html.index("async function fillBookEffects(")
    assert "r.failed" in html[i:i + 2500]


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #
def test_a_strategy_that_beats_qqq_and_holds_its_drop_passes_b1_and_g3():
    # Gold rises steadily with small moves; QQQ is flat and volatile; T-bills crawl.
    gold = _walk(1, drift=0.001, vol=0.004)
    paths = {"GLD": gold, "QQQ": _walk(2, drift=0.0, vol=0.02), "BIL": _walk(3, drift=0.0001, vol=0.0001)}
    r = gate.evaluate_blocking("D3", None, ratio_gain=0.02, drop_tol=2.0, fetch=_fetch_from(paths), long=False)
    assert r["ok"], r
    assert r["tests"]["B1"]["alone"] is True and r["tests"]["B1"]["pass"] is True
    assert r["tests"]["G3"]["pass"] is True
    assert r["tests"]["G1"]["badge"] == "different engine"
    assert r["measured_risk"] and r["expected_risk"] == "Medium"
    # No book to measure against: G2 cannot pass, so it is not offered.
    assert r["tests"]["G2"]["pass"] is False and r["passes_before_g6"] is False


def test_a_strategy_that_trails_qqq_and_cannot_lift_the_book_fails_b1():
    paths = {"IEF": _walk(4, drift=0.00005, vol=0.003), "QQQ": _walk(5, drift=0.0008, vol=0.012),
             "BIL": _walk(6, drift=0.0001, vol=0.0001)}
    r = gate.evaluate_blocking("D4", None, ratio_gain=0.02, drop_tol=2.0, fetch=_fetch_from(paths), long=False)
    assert r["tests"]["B1"]["pass"] is False and r["tests"]["B1"]["for_the_book"] is None


def test_a_plain_fund_has_no_sweep_and_a_young_one_gets_the_short_history_badge():
    r = gate.evaluate_blocking("D7", None, ratio_gain=0.02, drop_tol=2.0, fetch=_fetch_from({}), long=False)
    assert r["tests"]["G5"]["pass"] is None
    assert r["tests"]["G4"]["badge"] == "short history"


def test_near_duplicates_are_found_above_090():
    g = _walk(7, drift=0.0005, vol=0.01)
    paths = {"GLD": g, "UGL": 2 * g, "IEF": _walk(8), "BIL": _walk(9, drift=0.0001, vol=0.0001)}
    out = gate.near_duplicates_blocking(["D3", "D12", "D4"], fetch=_fetch_from(paths))
    assert {(d["a"], d["b"]) for d in out["near_duplicates"]} == {("D3", "D12")}


def test_the_candidate_list_is_served():
    with TestClient(m.app) as c:
        j = c.get("/api/v1/strategies/diversifiers").json()
    assert len(j["candidates"]) == 13 and j["candidates"][10]["expected_risk"] == "High to Very high"


def test_an_unknown_candidate_is_refused():
    assert gate.evaluate_blocking("D99", None, ratio_gain=0.02, drop_tol=2.0)["ok"] is False


@pytest.mark.parametrize("code", ["D1", "D2", "D11"])
def test_multi_leg_candidates_hold_t_bills_when_nothing_trends(code):
    """Every leg below its 200-day: the whole sleeve sits in T-bills, never in a falling market."""
    falling = {t: _walk(10 + i, drift=-0.002, vol=0.002) for i, t in
               enumerate(["SPY", "EFA", "IEF", "DBC", "VNQ", "TLT", "GLD", "SSO", "UBT", "UGL"])}
    falling["BIL"] = _walk(30, drift=0.0001, vol=0.0001)
    spec = dc.spec(code)
    series, _ = _fetch_from(falling)(bt.tickers_needed(spec))
    dates, px = bt.align(series)
    t = bt.targets_for(px, spec)
    assert set(t[-1]) <= {"BIL"}, t[-1]
