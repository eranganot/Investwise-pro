"""PR 126 -- the selection engine for the Beat the Market expansion.

`rank_select` ranks a universe and holds the winners; its differences across
the plan's strategies are settings. Around it: late listings join the calendar
instead of cutting it, one-day bad prints are dropped, the backtest starts on a
strategy's first live day, a selection strategy reports its edge over holding
every candidate equally, a fundamentals rule is measured by proxy and says so,
and gate test 3 judges decay against the strategy's own instruments.

Plan: docs/plans/BEAT_MARKET_EXPANSION_2026-10-13.md. Not financial advice.
"""
import numpy as np
import pytest

from app.engines import strategy_backtest as bt


def _days(n, start=0):
    import datetime as dt
    d0 = dt.date(2015, 1, 1)
    out, d = [], d0
    while len(out) < n + start:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out[start:]


def _walk(n, drift, vol=0.01, seed=1, start=100.0):
    rng = np.random.default_rng(seed)
    return start * np.cumprod(1 + rng.normal(drift, vol, n))


def _series(arr, days):
    return [(d, float(v)) for d, v in zip(days, arr)]


# --- calendar and data hygiene --------------------------------------------------

def test_a_late_listing_joins_the_calendar_instead_of_cutting_it():
    d = _days(600)
    ser = {"OLD": _series(_walk(600, 0.0005), d), "NEW": _series(_walk(300, 0.001), d[300:])}
    dates, px = bt.align(ser)
    assert len(dates) == 300, "the old behaviour: the 2021 listing cuts everything"
    dates, px = bt.align(ser, late={"NEW"})
    assert len(dates) == 600
    assert np.isnan(px["NEW"][:300]).all() and np.isfinite(px["NEW"][300:]).all()


def test_a_universe_of_late_joiners_alone_is_not_cut_to_its_youngest():
    d = _days(600)
    ser = {"OLD": _series(_walk(600, 0.0005), d), "NEW": _series(_walk(300, 0.001), d[300:])}
    dates, px = bt.align(ser, late={"OLD", "NEW"})
    assert len(dates) == 600 and np.isnan(px["NEW"][0])


def test_a_bad_print_is_dropped_and_a_crash_day_is_not():
    tsem = [("2017-05-03", 7662.0), ("2017-05-04", 1066.5), ("2017-05-08", 8355.0)]
    rows, k = bt.clean_spikes(tsem)
    assert k == 1 and [r[0] for r in rows] == ["2017-05-03", "2017-05-08"]
    tecl = [("2020-03-11", 15.01), ("2020-03-12", 10.60), ("2020-03-13", 14.36),
            ("2020-03-16", 9.17)]
    rows, k = bt.clean_spikes(tecl)
    assert k == 0, "a 3x fund's real crash days were dropped as bad prints"


# --- scores -----------------------------------------------------------------------

def test_twelve_one_momentum_skips_the_latest_month():
    a = np.full(300, 100.0)
    a[-1] = 200.0                       # a jump TODAY must not count
    assert bt.score_at(a, 299, "mom_12_1", {}) == pytest.approx(0.0)
    a[299 - 21] = 150.0
    assert bt.score_at(a, 299, "mom_12_1", {}) == pytest.approx(0.5)


def test_a_score_needs_its_history():
    a = np.full(300, np.nan)
    a[100:] = 100.0
    assert bt.score_at(a, 299, "mom_12_1", {}) is None
    assert bt.score_at(a, 299, "ret", {"lookback_days": 126}) is not None


def test_nearness_to_the_52_week_high():
    a = np.linspace(100, 200, 300)
    assert bt.score_at(a, 299, "high_52w", {}) == pytest.approx(1.0)
    a[-1] = 150.0
    assert bt.score_at(a, 299, "high_52w", {}) < 1.0


def test_low_volatility_ranks_the_calm_name_higher():
    calm, wild = _walk(300, 0, 0.005, 2), _walk(300, 0, 0.03, 3)
    assert bt.score_at(calm, 299, "low_vol", {}) > bt.score_at(wild, 299, "low_vol", {})


def test_the_buffer_keeps_a_held_name_until_it_drops_far_enough():
    ranked = ["A", "B", "C", "D", "E"]
    assert bt._pick(ranked, ["D"], top=2, keep_rank=4) == ["D", "A"]
    assert bt._pick(ranked, ["E"], top=2, keep_rank=4) == ["A", "B"]


# --- rank_select --------------------------------------------------------------------

def _px(n=700, names=("W", "X", "Y", "Z"), drifts=(0.002, 0.001, 0.0, -0.001)):
    px = {t: _walk(n, dr, 0.004, seed=k + 10) for k, (t, dr) in enumerate(zip(names, drifts))}
    px["QQQ"] = _walk(n, 0.0005, 0.01, seed=99)
    return px


def test_it_holds_the_top_names_and_the_rest_rides_in_the_base():
    px = _px()
    spec = {"weights": {}, "base": {"QQQ": 1.0},
            "overlay": {"kind": "rank_select", "universe": ["W", "X", "Y", "Z"],
                        "score": "mom_12_1", "top_n": 2, "cadence_days": 21}}
    tg = bt.targets_for(px, spec)
    last = tg[-1]
    assert set(last) == {"W", "X"} and sum(last.values()) == pytest.approx(1.0)
    assert all(t == {"QQQ": 1.0} for t in tg[:252]), "picked before the score had a year"


def test_an_unfilled_slot_falls_to_the_base():
    px = _px()
    spec = {"weights": {}, "base": {"QQQ": 1.0},
            "overlay": {"kind": "rank_select", "universe": ["W", "X", "Y", "Z"],
                        "score": "mom_12_1", "top_n": 6, "require_positive": True}}
    last = bt.targets_for(px, spec)[-1]
    held = {k: v for k, v in last.items() if k != "QQQ"}
    assert held and last["QQQ"] == pytest.approx(1 - len(held) / 6)


def test_the_market_gate_sends_the_sleeve_to_its_base():
    px = _px()
    px["SPY"] = np.concatenate([np.linspace(100, 200, 500), np.linspace(200, 100, 200)])
    spec = {"weights": {}, "base": {"QQQ": 1.0},
            "overlay": {"kind": "rank_select", "universe": ["W", "X"], "score": "mom_12_1",
                        "top_n": 1, "market_gate": {"ticker": "SPY", "ma_days": 200,
                                                    "confirm_days": 3}}}
    tg = bt.targets_for(px, spec)
    assert tg[-1] == {"QQQ": 1.0}
    assert tg[450] != {"QQQ": 1.0}


def test_a_late_name_is_not_picked_before_it_can_be_scored():
    n = 700
    px = _px(n)
    px["NEW"] = np.full(n, np.nan)
    px["NEW"][500:] = _walk(200, 0.02, 0.001, seed=5)       # a rocket, but young
    spec = {"weights": {}, "base": {"QQQ": 1.0},
            "overlay": {"kind": "rank_select", "universe": ["W", "X", "NEW"],
                        "score": "mom_12_1", "top_n": 1}}
    assert all("NEW" not in t for t in bt.targets_for(px, spec))


def test_two_stage_needs_enough_names_in_a_sector():
    px = _px()
    for etf, dr in (("S1", 0.003), ("S2", 0.002), ("S3", 0.001)):
        # Fixed seeds. This was hash(etf) % 100, and str hashes are randomised
        # per process (PYTHONHASHSEED): the seeds changed every run, and the
        # pair S2=10, S3=35 makes S3 the held sector (failed 1 of 3 full runs
        # on 2026-11-05; a sweep of all 10,000 pairs finds exactly that one).
        px[etf] = _walk(700, dr, 0.002, seed={"S1": 1, "S2": 2, "S3": 3}[etf])
    spec = {"weights": {}, "base": {"QQQ": 1.0},
            "overlay": {"kind": "rank_select", "score": "mom_12_1",
                        "two_stage": {"sectors": {"S1": ["W"], "S2": ["X", "Y"], "S3": ["Z", "W"]},
                                      "top_sectors": 1, "per_sector": 2, "min_names": 2}}}
    last = bt.targets_for(px, spec)[-1]
    assert "W" not in last or "Z" in last, "a one-stock sector was chosen"
    assert set(last) - {"QQQ"} == {"X", "Y"}


def test_a_signal_on_the_index_buys_the_fund():
    px = _px()
    px["LEVW"] = px["W"] * 1.0
    spec = {"weights": {}, "base": {"QQQ": 1.0},
            "overlay": {"kind": "rank_select", "universe": ["W", "X"], "score": "mom_12_1",
                        "top_n": 1, "hold_map": {"W": "LEVW", "X": "X"}}}
    assert set(bt.targets_for(px, spec)[-1]) == {"LEVW"}


def test_with_no_positive_pick_the_fallback_is_held():
    px = _px(names=("W", "X", "T", "B"), drifts=(-0.002, -0.001, 0.0005, 0.0001))
    spec = {"weights": {}, "base": {},
            "overlay": {"kind": "rank_select", "universe": ["W", "X"], "score": "blend",
                        "top_n": 1, "require_positive": True, "fallback": ["T", "B"]}}
    last = bt.targets_for(px, spec)[-1]
    assert set(last) <= {"T", "B"} and len(last) == 1


# --- measurement --------------------------------------------------------------------

def _run_series(n=800):
    d = _days(n)
    px = _px(n)
    return {t: _series(a, d) for t, a in px.items()}


def test_a_selection_strategy_reports_its_edge_and_its_bias():
    spec = {"id": "t", "weights": {}, "base": {"QQQ": 1.0}, "drift_band": 0.10,
            "overlay": {"kind": "rank_select", "universe": ["W", "X", "Y", "Z"],
                        "score": "mom_12_1", "top_n": 2}}
    r = bt.run(_run_series(), spec)
    assert r["ok"], r
    assert r["selection_edge_pct"] == pytest.approx(r["cagr_pct"] - r["equal_hold_cagr_pct"], abs=0.02)
    assert "survivorship" in r and r["universe_size"] == 4


def test_the_backtest_starts_on_the_first_live_day():
    spec = {"id": "t", "weights": {}, "base": {},
            "overlay": {"kind": "rank_select", "universe": ["W", "X", "Y", "Z"],
                        "score": "mom_12_1", "top_n": 2}}
    r = bt.run(_run_series(), spec)
    assert r["ok"] and r["observations"] < 800 - 250, r.get("observations")


def test_test_three_is_judged_against_the_strategys_own_instruments():
    """A strategy that is just its instrument has no excess decay to explain."""
    d = _days(900)
    up = np.linspace(100, 400, 600)
    down = np.linspace(400, 300, 300)
    lev = np.concatenate([up, down])
    ser = {"LEV": _series(lev, d), "SPY": _series(np.linspace(100, 150, 900), d)}
    spec = {"id": "t", "weights": {"LEV": 1.0}, "overlay": {"kind": "buy_hold"}}
    o = bt.out_of_sample(ser, spec, split_date=d[600], benchmark=ser["SPY"])
    assert o["verdict_vs_benchmark"] == "likely overfitted", o
    assert o["verdict"] == "holds up" and o["own_hold_decay_pct"] is not None


def test_own_hold_for_a_ranking_is_every_candidate():
    spec = {"id": "x", "weights": {}, "overlay": {"kind": "rank_select",
            "universe": ["A", "B"], "hold_map": {"A": "LA"}, "cadence_days": 63}}
    own = bt.own_hold_spec(spec)
    assert own["overlay"] == {"kind": "equal_hold", "universe": ["LA", "B"], "cadence_days": 63}


def test_a_proxy_measured_strategy_says_so(monkeypatch):
    from app.services import backtest_service as bs

    d = _days(800)
    monkeypatch.setattr(bs, "_fetch", lambda tickers, **kw: (
        {t: _series(_walk(800, 0.0004, 0.01, seed=len(t)), d) for t in tickers}, []))
    r = bs.measure({"id": "mf", "weights": {}, "proxy": {"ticker": "QVAL"},
                    "overlay": {"kind": "buy_hold"}})
    assert r["ok"] and r["measured_by_proxy"] == "QVAL"


def test_the_engine_version_moved():
    from app.services import backtest_service as bs
    assert bs.ENGINE_VERSION == "a7"
