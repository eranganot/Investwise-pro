"""A1 (owner, 2026-10-24): the goal model reads a FIXED stop as what it is, and the goal
is held against SPY.

Live on 2e86e36: TQQQ's stop at 65.73 with the price at 66.29 was replayed as a 0.8%
TRAILING stop for five years (`rules_spec` turned every price stop into `1 - level/price`
of trail). The TQQQ sleeve came out of the market 96% of the time, 3.7 exits a year; the
SOXL sleeve 88%. A real fixed stop sells once, at its price, and does not trail.
"""
import numpy as np

from app.engines import paths
from app.services import frontier_service as fs
from tests.test_frontier_resize import EST_KW, _prep

TQQQ_STOP = {"id": "s1", "ticker": "TQQQ", "rule_type": "stop_loss", "mode": "price", "level": 65.73}
TQQQ_TRAIL = {"id": "s2", "ticker": "TQQQ", "rule_type": "trailing_stop", "mode": "pct", "level": 20.0}


def _spec(rules):
    return fs.rules_spec(rules, prices={"TQQQ": 66.29}, sleeve_tickers={"vol": {"TQQQ", "QQQ"}})["sleeves"]["vol"]


def test_a_price_stop_is_a_one_time_level_not_a_trail():
    sp = _spec([TQQQ_STOP])
    assert sp["trail"] is None, "a fixed stop is not a trailing stop"
    assert sp["stop_ratio"] == round(65.73 / 66.29, 10) or abs(sp["stop_ratio"] - 65.73 / 66.29) < 1e-9
    assert "sells once at 65.73" in sp["rules"][0]
    both = _spec([TQQQ_STOP, TQQQ_TRAIL])
    assert both["trail"] == 0.20 and abs(both["stop_ratio"] - 65.73 / 66.29) < 1e-9


def _replay(sleeve):
    rng = np.random.default_rng(7)
    r = np.column_stack([rng.normal(6e-4, 0.04, 2000), rng.normal(3e-4, 0.01, 2000), np.zeros(2000)])
    return paths.simulate_rules(r, np.array([0.4, 0.55, 0.05]), years=5.0, sleeves=[{"col": 0, **sleeve}],
                                n_paths=300, seed=3)


def test_a_fixed_stop_fires_at_most_once_where_a_trail_at_the_same_distance_fires_again_and_again():
    once = _replay({"stop_ratio": 65.73 / 66.29})
    trail = _replay({"trail": 1 - 65.73 / 66.29})
    assert once["exits_per_year"][0] * 5.0 <= 1.0 + 1e-9, once["exits_per_year"]
    assert trail["exits_per_year"][0] > 3 * once["exits_per_year"][0], (trail["exits_per_year"], once["exits_per_year"])
    assert once["pct_time_out"][0] < trail["pct_time_out"][0]


def test_the_goal_is_held_against_spy_when_the_solve_has_it():
    prep = _prep()
    rng = np.random.default_rng(11)
    prep["bench_ils"] = rng.normal(4e-4, 0.011, prep["ils"].shape[0])
    v = fs.goal_solve(prep, sleeves_pct={"lev": 30.0, "calm": 3.0}, core_basket={"VTI": 0.6, "BND": 0.4},
                      cash_pct=4.0, objective="Grow", est_kw=EST_KW, nav=10_000, target=25_000, years=5.0,
                      draws=0, light=True)
    assert v["benchmark"]["ticker"] == "SPY" and v["benchmark"]["label"] == "the S&P 500"
    assert "S&P 500 (SPY)" in fs.benchmark_line({**v["benchmark"], "roi_at_confidence_pct": 8.0},
                                                {"roi_at_confidence_pct": 4.0}, star=65)
    del prep["bench_ils"]
    v2 = fs.goal_solve(prep, sleeves_pct={"lev": 30.0, "calm": 3.0}, core_basket={"VTI": 0.6, "BND": 0.4},
                       cash_pct=4.0, objective="Grow", est_kw=EST_KW, nav=10_000, target=25_000, years=5.0,
                       draws=0, light=True)
    assert v2["benchmark"]["ticker"] == "VTI", "without SPY history the US-market column stands in"
