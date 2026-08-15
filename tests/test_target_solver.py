"""T2 - the solver, and its right to say no.

Every assertion is on a measured verdict, not on wording. The load-bearing ones
are the constraint tests: a returned weight must re-measure as satisfying the
target AND the drawdown ceiling, and lowering the ceiling must never raise the
answer.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.engines import blend
from app.services import target_solver as ts

N = 700


def _dates(n: int = N) -> list[str]:
    return [f"{2016 + i // 252:04d}-{(i % 252) // 21 + 1:02d}"
            f"-{(i % 21) + 1:02d}-{i:04d}" for i in range(n)]


DATES = _dates()


def _series(start: float, growth: float, wobble: float = 0.0):
    i = np.arange(N)
    path = start * (1.0 + growth) ** (i / 252.0)
    if wobble:
        path = path * (1.0 + wobble * np.sin(i / 40.0))
    return list(zip(DATES, [float(x) for x in path]))


HOT = _series(100.0, 0.30, 0.18)      # strong sleeve, deep wobble
MILD = _series(100.0, 0.09, 0.05)     # core
BENCH = _series(100.0, 0.10, 0.05)
FLAT = _series(100.0, 0.02, 0.03)     # a sleeve that cannot beat the benchmark

SERIES = {"HOT": HOT, "MILD": MILD, "FLAT": FLAT}


def _sleeve(tk="HOT", sid="sleeve", current=10.0):
    return {"id": sid,
            "spec": {"id": sid, "weights": {tk: 1.0},
                     "overlay": {"kind": "buy_hold"}},
            "current_pct": current}


CORE = blend.core_spec({"MILD": 1.0})


def _solve(target, ceiling, sleeves=None, cap=1.0, floor=0.0):
    return ts.solve(SERIES, sleeves=sleeves or [_sleeve()], core=CORE,
                    benchmark=BENCH, target_excess_pct=target,
                    max_drawdown_pct=ceiling, cash_floor=floor,
                    concentration_cap=cap)


# ------------------------------------------------------------- reachable
def test_a_reachable_target_returns_a_weight_that_re_measures_as_meeting_it():
    v = _solve(target=2.0, ceiling=100.0)
    assert v["outcome"] == ts.REACHED
    s = v["solved_total_sleeve_pct"]
    assert 0 < s <= 100

    # Re-measure independently: the verdict must survive being checked.
    comps = [{"id": "sleeve", "spec": _sleeve()["spec"], "weight": s / 100.0},
             {"id": "__core__", "spec": CORE, "weight": (100 - s) / 100.0}]
    again = blend.measure_blend(SERIES, comps, benchmark=BENCH)
    assert again["excess_cagr_pct"] >= 2.0


def test_the_displayed_size_rounds_up_never_below_the_target():
    v = _solve(target=2.0, ceiling=100.0)
    assert v["display_total_sleeve_pct"] >= v["solved_total_sleeve_pct"]
    assert v["display_total_sleeve_pct"] % ts.DISPLAY_STEP_PCT == 0


def test_it_returns_the_smallest_weight_that_works_not_the_largest():
    v = _solve(target=1.0, ceiling=100.0)
    s = v["solved_total_sleeve_pct"]
    lower = [p for p in v["curve"]
             if p["total_sleeve_pct"] < s and p["admissible"]
             and p["excess_pct"] is not None and p["excess_pct"] >= 1.0]
    assert lower == []


# ------------------------------------------------------- drawdown ceiling
def test_the_drawdown_ceiling_constrains_the_answer_not_the_report():
    """A returned weight must satisfy the ceiling, not merely mention it."""
    v = _solve(target=1.0, ceiling=100.0)
    assert v["outcome"] == ts.REACHED
    loose = v["solved_total_sleeve_pct"]

    tight = _solve(target=1.0, ceiling=v["measured"]["max_drawdown_pct"] * 0.5)
    if tight["outcome"] == ts.REACHED:
        assert tight["measured"]["max_drawdown_pct"] <= \
            v["measured"]["max_drawdown_pct"] * 0.5 + 1e-9
        # Monotonicity: a tighter ceiling can never permit a LARGER sleeve.
        assert tight["solved_total_sleeve_pct"] <= loose
    else:
        assert tight["outcome"] in (ts.DRAWDOWN_BOUND, ts.UNREACHABLE)


def test_a_target_reachable_only_by_breaching_the_ceiling_says_so():
    """The most informative outcome: your risk tolerance is what binds, not
    your strategies."""
    loose = _solve(target=3.0, ceiling=100.0)
    assert loose["outcome"] == ts.REACHED
    needed_dd = loose["measured"]["max_drawdown_pct"]

    v = _solve(target=3.0, ceiling=needed_dd * 0.75)
    assert v["outcome"] == ts.DRAWDOWN_BOUND
    b = v["binding_constraint"]
    assert b["kind"] == "drawdown_ceiling"
    assert b["would_require_pct"] > b["ceiling_pct"]
    # There IS a best size inside the ceiling here, and it must satisfy it.
    assert v["best_within_ceiling"] is not None
    assert v["best_within_ceiling"]["max_drawdown_pct"] <= b["ceiling_pct"] + 1e-9
    assert v["floor"]["breaches_ceiling"] is False


def test_a_ceiling_below_the_core_itself_says_the_core_is_the_problem():
    """Nothing admissible can mean two opposite things: the sleeves are too
    risky, or the book already breaches the ceiling holding no sleeve at all.
    Those need opposite responses, so the difference has to be reported."""
    zero = _solve(target=99.0, ceiling=100.0)
    core_only = min(zero["curve"], key=lambda p: p["total_sleeve_pct"])

    v = _solve(target=3.0, ceiling=core_only["max_drawdown_pct"] * 0.5)
    assert v["floor"]["breaches_ceiling"] is True
    assert v["floor"]["note"]
    assert v["best_within_ceiling"] is None      # honestly none, not a guess
    assert not any(p["admissible"] for p in v["curve"])


# ---------------------------------------------------- concentration cap
def test_a_target_reachable_only_past_the_cap_names_the_cap_and_the_ticker():
    v = _solve(target=3.0, ceiling=100.0, cap=0.10)
    assert v["outcome"] in (ts.REACHED_ABOVE_CAP, ts.UNREACHABLE)
    if v["outcome"] == ts.REACHED_ABOVE_CAP:
        b = v["binding_constraint"]
        assert b["kind"] == "concentration_cap"
        assert b["cap_pct"] == pytest.approx(10.0)
        assert b["ticker"] == "HOT"
        assert b["would_reach_pct"] > b["cap_pct"]


# ------------------------------------------------------------ unreachable
def test_an_unreachable_target_names_the_component_holding_it_down():
    weak = [_sleeve(tk="FLAT", sid="flat", current=10.0)]
    v = _solve(target=25.0, ceiling=100.0, sleeves=weak)
    assert v["outcome"] == ts.UNREACHABLE
    b = v["binding_constraint"]
    assert b["kind"] == "component_excess"
    assert b["component"] == "flat"
    # It reports the sleeve's OWN measured excess, which is the actionable part.
    assert b["component_excess_pct"] < 25.0


def test_unreachable_still_reports_the_best_that_was_achievable():
    weak = [_sleeve(tk="FLAT", sid="flat")]
    v = _solve(target=25.0, ceiling=100.0, sleeves=weak)
    assert v["measured"] is not None
    assert v["measured"]["excess_cagr_pct"] < 25.0


# ------------------------------------------------------------- read-only
def test_the_solver_never_returns_an_execution_plan():
    for target, ceiling in ((1.0, 100.0), (25.0, 100.0), (3.0, 1.0)):
        v = _solve(target=target, ceiling=ceiling)
        assert v["execution_plan"] is None


def test_would_execute_describes_the_diff_and_prices_nothing():
    v = _solve(target=2.0, ceiling=100.0)
    we = v["would_execute"]
    assert [r["strategy_id"] for r in we["resizes"]] == ["sleeve"]
    assert we["resizes"][0]["from_pct"] == 10.0
    assert we["resizes"][0]["to_pct"] == pytest.approx(
        v["solved_total_sleeve_pct"], abs=0.01)
    # A priced leg in a read-only phase would manufacture a stale-quote claim.
    assert we["legs"] is None
    assert "price_as_of" in we["legs_schema"]


# ------------------------------------------------------------- abstains
def test_no_sleeves_is_an_abstention_not_a_zero():
    v = ts.solve(SERIES, sleeves=[], core=CORE, benchmark=BENCH,
                 target_excess_pct=1.0, max_drawdown_pct=50.0)
    assert v["outcome"] == ts.NOT_MEASURABLE
    assert v["reason"] == "NO_SLEEVES"


def test_an_unmeasurable_blend_abstains_with_the_engine_reason():
    short = {tk: rows[:40] for tk, rows in SERIES.items()}
    v = ts.solve(short, sleeves=[_sleeve()], core=CORE, benchmark=BENCH,
                 target_excess_pct=1.0, max_drawdown_pct=50.0)
    assert v["outcome"] == ts.NOT_MEASURABLE
    assert v["reason"] == blend.INSUFFICIENT_HISTORY


# ------------------------------------------------------------ cash floor
def test_the_cash_floor_caps_what_can_be_allocated():
    v = _solve(target=0.1, ceiling=100.0, floor=0.20)
    assert max(p["total_sleeve_pct"] for p in v["curve"]) <= 80.0 + 1e-9


# ----------------------------------------------------------- the report
def test_the_reported_point_is_measured_in_full_not_from_the_sweep():
    """The sweep runs detail=False. The point on the card must not."""
    v = _solve(target=1.0, ceiling=100.0)
    assert v["measured"]["detailed"] is True
    assert v["measured"]["cash_drag_pct"] is not None
    assert v["measured"]["excess_at_equal_risk_pct"] is not None


def test_the_curve_marks_which_points_were_admissible():
    v = _solve(target=1.0, ceiling=100.0, cap=0.10)
    assert any(p["admissible"] is False for p in v["curve"])
    assert all({"total_sleeve_pct", "excess_pct", "max_drawdown_pct",
                "admissible"} <= set(p) for p in v["curve"])
