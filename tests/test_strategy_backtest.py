"""Strategy backtest engine - deterministic tests on synthetic series (no network).

Every series here is constructed so the expected answer is knowable by hand:
a straight-line riser, a V-shaped crash, a sawtooth. That way a failure means
the engine is wrong rather than that the market was unusual.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from app.engines import strategy_backtest as bt


def _dates(n: int) -> list[str]:
    d0 = dt.date(2015, 1, 2)
    return [(d0 + dt.timedelta(days=int(i * 1.4))).isoformat() for i in range(n)]


def _series(values) -> list[tuple[str, float]]:
    return list(zip(_dates(len(values)), [float(v) for v in values]))


def _compound(rets) -> np.ndarray:
    return 100.0 * np.cumprod(1.0 + np.asarray(rets, dtype=float))


N = 1400
_UP = _compound(np.full(N, 0.0005))                       # steady riser
_FLAT = _compound(np.full(N, 0.03 / 252))                 # cash-like


def _crash_path(seed: int = 3) -> np.ndarray:
    """Up, a deep sustained drawdown, then recovery - a regime a filter should dodge."""
    rng = np.random.default_rng(seed)
    r = np.where((np.arange(N) > 500) & (np.arange(N) < 800), -0.003, 0.0008)
    return _compound(r + rng.normal(0, 0.010, N))


# ---------------------------------------------------------------- indicators

def test_sma_matches_a_hand_computed_window():
    a = np.arange(1.0, 11.0)
    out = bt.sma(a, 3)
    assert np.isnan(out[:2]).all()          # undefined before the window fills
    assert out[2] == pytest.approx(2.0)     # (1+2+3)/3
    assert out[-1] == pytest.approx(9.0)    # (8+9+10)/3


def test_rsi_pins_to_100_when_every_move_is_up():
    assert bt.rsi(np.arange(1.0, 40.0), 2)[-1] == pytest.approx(100.0)


def test_rsi_is_low_after_a_run_of_losses():
    a = np.concatenate([np.arange(1.0, 30.0), np.arange(29.0, 20.0, -1.0)])
    assert bt.rsi(a, 2)[-1] < 20.0


def test_confirmation_delays_the_flip_and_ignores_a_blip():
    flag = np.array([True, True, True, False, True, True, True, True])
    out = bt._confirm(flag, 3)
    assert not out[1] and out[2]            # needs three in a row
    assert not out[4] and not out[5]        # the blip reset the run
    assert out[6]


def test_rolling_max_and_min_track_the_channel():
    a = np.array([1.0, 5.0, 3.0, 2.0, 9.0])
    assert bt.rolling_max(a, 3)[-1] == 9.0
    assert bt.rolling_min(a, 3)[-1] == 2.0


# ---------------------------------------------------------------- alignment

def test_align_intersects_calendars_and_drops_unshared_dates():
    a = [("2020-01-01", 1.0), ("2020-01-02", 2.0), ("2020-01-03", 3.0)]
    b = [("2020-01-02", 9.0), ("2020-01-03", 8.0), ("2020-01-06", 7.0)]
    dates, px = bt.align({"A": a, "B": b})
    assert dates == ["2020-01-02", "2020-01-03"]
    assert list(px["B"]) == [9.0, 8.0]


def test_tickers_needed_collects_every_referenced_symbol():
    spec = {"weights": {"LEV": 1.0}, "risk_off": "BIL",
            "overlay": {"kind": "rsi_pullback", "gate_ticker": "IDX", "signal_ticker": "LEV"}}
    assert bt.tickers_needed(spec) == ["BIL", "IDX", "LEV"]


# ---------------------------------------------------------------- abstention

def test_missing_ticker_abstains_rather_than_guessing():
    r = bt.run({"IDX": _series(_UP)}, {"weights": {"NOPE": 1.0}})
    assert r["ok"] is False and r["reason"] == bt.MISSING_TICKER


def test_short_history_abstains_with_a_count_in_the_detail():
    r = bt.run({"IDX": _series(_UP[:40])}, {"weights": {"IDX": 1.0}})
    assert r["reason"] == bt.INSUFFICIENT_HISTORY and "40" in r["detail"]


def test_unknown_overlay_abstains():
    r = bt.run({"IDX": _series(_UP)}, {"weights": {"IDX": 1.0}, "overlay": {"kind": "wat"}})
    assert r["reason"] == bt.UNKNOWN_OVERLAY


def test_a_spec_with_no_basket_abstains():
    r = bt.run({"IDX": _series(_UP)}, {"weights": {}})
    assert r["reason"] == bt.BAD_SPEC


def test_non_overlapping_calendars_abstain():
    a = [(f"2020-01-{d:02d}", 1.0) for d in range(1, 15)]
    b = [(f"2021-01-{d:02d}", 1.0) for d in range(1, 15)]
    r = bt.run({"A": a, "B": b}, {"weights": {"A": 1.0, "B": 1.0}})
    assert r["reason"] in (bt.NO_OVERLAP, bt.INSUFFICIENT_HISTORY)


# ---------------------------------------------------------------- simulation

def test_buy_and_hold_recovers_the_underlying_cagr():
    r = bt.run({"IDX": _series(_UP)}, {"weights": {"IDX": 1.0}})
    assert r["ok"]
    expected = ((_UP[-1] / _UP[0]) ** (252 / (len(_UP) - 1)) - 1) * 100
    assert r["cagr_pct"] == pytest.approx(expected, abs=0.5)


def test_a_riser_never_realizes_a_gain_so_pays_no_tax():
    r = bt.run({"IDX": _series(_UP)}, {"weights": {"IDX": 1.0}})
    assert r["tax_paid_pct_of_start"] == pytest.approx(0.0, abs=1e-6)
    assert r["tax_drag_pct"] == pytest.approx(0.0, abs=0.01)


def test_tax_drag_is_positive_once_a_strategy_actually_trades():
    S = {"IDX": _series(_crash_path()), "BIL": _series(_FLAT)}
    spec = {"weights": {"IDX": 1.0}, "risk_off": "BIL",
            "overlay": {"kind": "ma_cross", "gate_ticker": "IDX", "fast": 10, "slow": 30}}
    r = bt.run(S, spec)
    assert r["ok"] and r["trades_per_year"] > 1
    assert r["gross_cagr_pct"] >= r["cagr_pct"]      # tax can only subtract


def test_zero_cgt_and_zero_costs_reproduce_the_gross_path():
    S = {"IDX": _series(_crash_path()), "BIL": _series(_FLAT)}
    spec = {"weights": {"IDX": 1.0}, "risk_off": "BIL",
            "overlay": {"kind": "donchian", "gate_ticker": "IDX"}}
    r = bt.run(S, spec, cgt_rate=0.0, cost_bps=0.0)
    assert r["cagr_pct"] == pytest.approx(r["gross_cagr_pct"], abs=1e-6)


def test_dealing_costs_reduce_return():
    S = {"IDX": _series(_crash_path()), "BIL": _series(_FLAT)}
    spec = {"weights": {"IDX": 1.0}, "risk_off": "BIL",
            "overlay": {"kind": "ma_cross", "gate_ticker": "IDX", "fast": 10, "slow": 30}}
    cheap = bt.run(S, spec, cost_bps=0.0)
    dear = bt.run(S, spec, cost_bps=50.0)
    assert dear["cagr_pct"] < cheap["cagr_pct"]


def test_a_trend_filter_beats_buy_and_hold_through_a_sustained_crash():
    path = _crash_path()
    S = {"IDX": _series(path), "BIL": _series(_FLAT)}
    held = bt.run(S, {"weights": {"IDX": 1.0}})
    gated = bt.run(S, {"weights": {"IDX": 1.0}, "risk_off": "BIL",
                       "overlay": {"kind": "trend_filter", "gate_ticker": "IDX",
                                   "ma_days": 100, "confirm_days": 2}})
    assert gated["max_drawdown_pct"] < held["max_drawdown_pct"]
    assert gated["cagr_pct"] > held["cagr_pct"]


def test_the_engine_is_deterministic():
    S = {"IDX": _series(_crash_path()), "BIL": _series(_FLAT)}
    spec = {"weights": {"IDX": 1.0}, "risk_off": "BIL",
            "overlay": {"kind": "donchian", "gate_ticker": "IDX"}}
    assert bt.run(S, spec) == bt.run(S, spec)


def test_signals_execute_a_day_late_so_there_is_no_lookahead():
    """A one-day spike the strategy could only see on its close must not be captured.

    Flat, then a single +50% day, then flat. A lookahead bug buys on the spike
    day itself and books the gain; correct behaviour buys the day after, at the
    already-elevated price, and earns nothing from it.
    """
    v = np.concatenate([np.full(700, 100.0), np.full(700, 150.0)])
    S = {"IDX": _series(v), "BIL": _series(np.full(1400, 100.0))}
    spec = {"weights": {"IDX": 1.0}, "risk_off": "BIL",
            "overlay": {"kind": "donchian", "gate_ticker": "IDX",
                        "entry_days": 20, "exit_days": 10}}
    r = bt.run(S, spec)
    assert r["ok"]
    assert r["total_return_pct"] < 1.0


def test_benchmark_excess_is_reported_against_the_supplied_series():
    S = {"IDX": _series(_UP)}
    r = bt.run(S, {"weights": {"IDX": 1.0}}, benchmark=_series(_FLAT))
    assert r["excess_cagr_pct"] == pytest.approx(
        r["cagr_pct"] - r["benchmark_cagr_pct"], abs=0.01)
    assert r["excess_cagr_pct"] > 0


def test_dual_momentum_sits_in_the_safe_asset_when_nothing_is_rising():
    falling = _compound(np.full(N, -0.001))
    S = {"A": _series(falling), "BIL": _series(_FLAT)}
    spec = {"weights": {"A": 1.0}, "risk_off": "BIL",
            "overlay": {"kind": "dual_momentum", "universe": ["A"],
                        "lookback_days": 252, "risk_off": "BIL"}}
    r = bt.run(S, spec)
    assert r["ok"] and r["cagr_pct"] > -1.0     # absolute-momentum veto held


# ---------------------------------------------------------------- guards

def test_out_of_sample_splits_and_returns_a_verdict():
    S = {"IDX": _series(_crash_path()), "BIL": _series(_FLAT)}
    spec = {"weights": {"IDX": 1.0}, "risk_off": "BIL",
            "overlay": {"kind": "trend_filter", "gate_ticker": "IDX", "ma_days": 100}}
    r = bt.out_of_sample(S, spec, split_date="2018-01-01")
    assert r["in_sample"]["ok"] and r["out_of_sample"]["ok"]
    assert r["verdict"] in ("holds up", "weaker out of sample", "likely overfitted")


def test_sweep_reports_a_spread_and_a_robustness_flag():
    S = {"IDX": _series(_crash_path()), "BIL": _series(_FLAT)}
    spec = {"weights": {"IDX": 1.0}, "risk_off": "BIL",
            "overlay": {"kind": "trend_filter", "gate_ticker": "IDX", "ma_days": 100}}
    r = bt.sweep(S, spec, "ma_days", [80, 100, 120, 140])
    assert len(r["results"]) == 4
    assert r["cagr_spread_pct"] >= 0
    assert isinstance(r["robust"], bool)


def test_every_successful_result_carries_the_not_a_forecast_disclaimer():
    r = bt.run({"IDX": _series(_UP)}, {"weights": {"IDX": 1.0}})
    assert "not a forecast" in r["disclaimer"]
    assert r["start"] and r["end"] and r["observations"] > 0
    assert r["cgt_rate_pct"] == 25.0
