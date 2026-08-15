"""T1 - the blend engine. Assertions are on the measured series, never on shape.

The invariant that matters most here is the identity one: a blend of a single
component at full weight must reproduce that component's own metrics EXACTLY.
If it does not, the blend path and the strategy path are measuring by two
different methods and every comparison the solver makes is meaningless.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.engines import blend
from app.engines import strategy_backtest as bt

N = 700          # comfortably over _MIN_OBSERVATIONS


def _dates(n: int = N) -> list[str]:
    # Synthetic but ordered; align() only needs sortable, comparable keys.
    return [f"{2016 + i // 252:04d}-{(i % 252) // 21 + 1:02d}"
            f"-{(i % 21) + 1:02d}-{i:04d}" for i in range(n)]


DATES = _dates()


def _series(start: float, cagr_like: float,
            wobble: float = 0.0) -> list[tuple[str, float]]:
    """A deterministic price path. `wobble` adds a drawdown to bite on."""
    i = np.arange(N)
    trend = start * (1.0 + cagr_like) ** (i / 252.0)
    if wobble:
        trend = trend * (1.0 + wobble * np.sin(i / 40.0))
    return list(zip(DATES, [float(x) for x in trend]))


AAA = _series(100.0, 0.20, 0.10)      # the "aggressive" leg
CORE = _series(100.0, 0.08, 0.04)     # the "core" leg
BENCH = _series(100.0, 0.10, 0.05)

SERIES = {"AAA": AAA, "CORE": CORE}
SPEC_A = {"id": "aaa", "weights": {"AAA": 1.0}, "overlay": {"kind": "buy_hold"}}
SPEC_C = {"id": "core", "weights": {"CORE": 1.0}, "overlay": {"kind": "buy_hold"}}


def _one(spec, weight=1.0, cid="x"):
    return [{"id": cid, "spec": spec, "weight": weight}]


# ------------------------------------------------------------------ identity
def test_single_component_at_full_weight_reproduces_the_strategy_exactly():
    """The blend path and the strategy path must be the same measurement."""
    solo = bt.run(SERIES, SPEC_A, benchmark=BENCH)
    mixed = blend.measure_blend(SERIES, _one(SPEC_A), benchmark=BENCH)

    assert solo["ok"] and mixed["ok"]
    for key in ("cagr_pct", "max_drawdown_pct", "total_return_pct",
                "volatility_pct", "observations", "excess_cagr_pct",
                "benchmark_cagr_pct"):
        assert mixed[key] == solo[key], key


def test_two_identical_components_at_half_weight_each_match_one_at_full():
    """Splitting a position in two must not change what the book measured."""
    a = blend.measure_blend(SERIES, _one(SPEC_A), benchmark=BENCH)
    b = blend.measure_blend(SERIES, [
        {"id": "a1", "spec": SPEC_A, "weight": 0.5},
        {"id": "a2", "spec": SPEC_A, "weight": 0.5},
    ], benchmark=BENCH)
    assert b["cagr_pct"] == pytest.approx(a["cagr_pct"], abs=0.02)
    assert b["max_drawdown_pct"] == pytest.approx(a["max_drawdown_pct"], abs=0.02)


# ------------------------------------------------------------------ blending
def test_same_ticker_from_two_components_sums_into_one_position():
    """The book holds ONE position per ticker; the blend must model that."""
    rows = blend.blend_targets(
        [[{"TQQQ": 1.0}] * 3, [{"TQQQ": 1.0}] * 3], [0.2, 0.15])
    assert rows[0] == {"TQQQ": pytest.approx(0.35)}


def test_unallocated_weight_stays_in_cash():
    rows = blend.blend_targets([[{"AAA": 1.0}] * 2], [0.3])
    assert sum(rows[0].values()) == pytest.approx(0.3)   # 70% is cash, not spread


def test_cash_remainder_is_reported_and_priced():
    out = blend.measure_blend(SERIES, _one(SPEC_A, weight=0.5), benchmark=BENCH)
    assert out["allocated_pct"] == 50.0
    assert out["cash_pct"] == 50.0
    # Holding half in cash must cost return on a rising asset -- and the cost is
    # reported rather than buried in an unattributable lower CAGR.
    assert out["cash_drag_pct"] > 0


# ------------------------------------------------------------------ abstains
def test_weights_over_one_hundred_percent_abstain():
    out = blend.measure_blend(SERIES, [
        {"id": "a", "spec": SPEC_A, "weight": 0.7},
        {"id": "c", "spec": SPEC_C, "weight": 0.6},
    ])
    assert out["ok"] is False and out["reason"] == blend.WEIGHTS_EXCEED_BOOK


def test_a_component_that_cannot_be_measured_names_itself():
    bad = {"id": "bad", "weights": {"AAA": 1.0}, "overlay": {"kind": "nonesuch"}}
    out = blend.measure_blend(SERIES, [{"id": "bad", "spec": bad, "weight": 0.2}])
    assert out["ok"] is False
    assert out["reason"] == blend.COMPONENT_ABSTAINED
    assert out["component"] == "bad"


def test_short_history_abstains_rather_than_reporting_noise():
    short = {tk: rows[:50] for tk, rows in SERIES.items()}
    out = blend.measure_blend(short, _one(SPEC_A))
    assert out["ok"] is False and out["reason"] == blend.INSUFFICIENT_HISTORY


def test_no_components_is_an_abstention_not_an_empty_book():
    out = blend.measure_blend(SERIES, [])
    assert out["ok"] is False and out["reason"] == blend.NO_COMPONENTS


# ------------------------------------------------------- diversification
def test_diversification_delta_is_measured_not_assumed():
    """Two legs that fall together diversify nothing, and the number says so."""
    out = blend.measure_blend(SERIES, [
        {"id": "a", "spec": SPEC_A, "weight": 0.3},
        {"id": "c", "spec": SPEC_C, "weight": 0.7},
    ], benchmark=BENCH)
    assert out["ok"]
    assert len(out["components"]) == 2
    # Correlated legs: the blended drawdown sits close to the weighted average.
    assert out["diversification_delta_pct"] == pytest.approx(0.0, abs=3.0)


# ------------------------------------------------------------- equal risk
def test_equal_risk_excess_is_not_inflated_by_leverage():
    """Doubling exposure must not improve the equal-risk verdict.

    This is the property the whole objective function rests on: raw excess is
    bought with leverage, equal-risk excess is not.
    """
    lo = blend.measure_blend(SERIES, _one(SPEC_A, weight=0.4), benchmark=BENCH)
    hi = blend.measure_blend(SERIES, _one(SPEC_A, weight=0.8), benchmark=BENCH)

    assert lo["excess_at_equal_risk_pct"] is not None
    assert hi["excess_at_equal_risk_pct"] is not None
    # Raw excess rises with exposure...
    assert hi["excess_cagr_pct"] > lo["excess_cagr_pct"]
    # ...the equal-risk verdict does not.
    assert hi["excess_at_equal_risk_pct"] == pytest.approx(
        lo["excess_at_equal_risk_pct"], abs=1.5)


def test_equal_risk_reports_the_leverage_and_the_drawdown_it_landed_on():
    out = blend.measure_blend(SERIES, _one(SPEC_A, weight=0.5), benchmark=BENCH)
    assert out["equal_risk_leverage"] > 0
    bench_dd = out["benchmark_max_drawdown_pct"]
    assert out["equal_risk_drawdown_pct"] == pytest.approx(bench_dd, abs=0.5)


def test_no_benchmark_means_no_equal_risk_claim():
    out = blend.measure_blend(SERIES, _one(SPEC_A, weight=0.5))
    assert out["ok"]
    assert out["excess_at_equal_risk_pct"] is None


# ------------------------------------------------------------------- core
def test_core_weights_exclude_every_ticker_a_sleeve_claims():
    held = {"V": 5000.0, "MSFT": 3000.0, "TQQQ": 2000.0}
    core = blend.core_weights_from(held, {"tqqq"})
    assert core == {"V": 5000.0, "MSFT": 3000.0}


def test_core_spec_normalizes_composition_and_leaves_scale_to_the_weight():
    spec = blend.core_spec({"V": 5000.0, "MSFT": 3000.0})
    assert spec["overlay"]["kind"] == "buy_hold"
    assert sum(spec["weights"].values()) == pytest.approx(1.0)
    assert spec["weights"]["V"] == pytest.approx(0.625)


def test_an_empty_core_is_empty_not_an_error():
    spec = blend.core_spec({})
    assert spec["weights"] == {}
