"""T6 - the split search, and the doubt that has to travel with it.

The load-bearing tests are `test_a_split_that_wins_in_sample_and_fails_out_is_called_failed`
and `test_an_optimum_indistinguishable_from_the_median_is_reported_as_noise`.

The search itself is arithmetic and either works or does not. The risk in this
phase is that it works TOO well: pick the best of 55 splits scored on one ten-year
window and something always wins, and the winner ends up under a green Accept
button looking exactly like a finding. These tests are what makes the app say so.
"""
from __future__ import annotations

from app.services import split_solver as ss


# ------------------------------------------------------------------ geometry
def test_the_grid_is_every_combination_that_fits():
    pts, step = ss.simplex_grid(2, 10.0, 30.0)
    assert step == 10.0
    assert set(pts) == {(a, b) for a in (0, 10, 20, 30) for b in (0, 10, 20, 30)
                        if a + b <= 30}
    assert all(sum(p) <= 30.0 + 1e-9 for p in pts)


def test_a_grid_too_large_is_coarsened_not_truncated():
    """Truncating would search a CORNER of the space and report the winner as
    though the whole space had been tried -- the exact claim `coverage` exists
    to prevent."""
    pts, step = ss.simplex_grid(3, 5.0, 100.0, max_points=100)
    assert step > 5.0, "the step should have been coarsened"
    assert len(pts) <= 100
    # Coarsened, not clipped: the space is still covered corner to corner.
    assert min(sum(p) for p in pts) == 0
    assert max(sum(p) for p in pts) >= 90


def test_the_grid_never_exceeds_the_cash_floor_adjusted_total():
    pts, _ = ss.simplex_grid(2, 10.0, 90.0)
    assert all(sum(p) <= 90.0 + 1e-9 for p in pts)
    # 40+50 = 90 fits exactly; 50+50 = 100 does not. (The first draft of this
    # test asserted (50,50) should be present -- it sums to 100 against a 90
    # ceiling, so the code was right and the test was wrong.)
    assert (40.0, 50.0) in pts and (50.0, 50.0) not in pts


def test_neighbours_can_trade_size_between_sleeves_at_a_constant_total():
    """'The same 65%, divided differently' is the question this phase was asked.
    Axis-only moves cannot express it."""
    n = ss.neighbours((30.0, 35.0), 5.0, 100.0)
    assert (35.0, 30.0) in n and (25.0, 40.0) in n
    assert all(abs(sum(p) - 65.0) < 1e-9 for p in [(35.0, 30.0), (25.0, 40.0)])


def test_neighbours_stay_inside_the_book():
    for p in ss.neighbours((0.0, 95.0), 10.0, 95.0):
        assert min(p) >= -1e-9 and sum(p) <= 95.0 + 1e-9


def test_a_point_is_not_its_own_neighbour():
    assert (30.0, 30.0) not in ss.neighbours((30.0, 30.0), 5.0, 100.0)


# -------------------------------------------------------------------- search
def _peak_at(target, ceiling=100.0):
    """A measure() whose excess peaks at `target` and respects a ceiling."""
    def measure(p):
        dist = sum(abs(a - b) for a, b in zip(p, target))
        return {"excess_pct": round(20.0 - 0.1 * dist, 4),
                "max_drawdown_pct": round(sum(p) * 0.5, 2),
                "admissible": sum(p) * 0.5 <= ceiling}
    return measure


def test_the_search_finds_a_peak_the_coarse_grid_missed():
    # Peak at (33, 31) is off the 10-point grid; refinement has to walk to it.
    out = ss.search(_peak_at((33.0, 31.0)), n=2, max_total=97.0)
    assert out["ok"]
    assert out["best"]["split_pct"] == [33.0, 31.0]
    assert out["refinement_moved"] is True


def test_the_result_is_never_called_a_global_optimum():
    out = ss.search(_peak_at((30.0, 30.0)), n=2, max_total=97.0)
    assert "not a proven global optimum" in out["coverage"]["method"]


def test_an_unmeasurable_point_is_recorded_not_scored_as_zero():
    """A failed simulation is not a bad result, and scoring it as one would make
    the search prefer whatever happened to be measurable."""
    def measure(p):
        if p == (10.0, 10.0):
            return None
        return {"excess_pct": 5.0, "max_drawdown_pct": 1.0, "admissible": True}
    out = ss.search(measure, n=2, max_total=30.0)
    assert out["coverage"]["unmeasurable"] >= 1
    assert out["best"]["excess_pct"] == 5.0


def test_nothing_admissible_is_an_answer_not_a_crash():
    def measure(p):
        return {"excess_pct": 9.0, "max_drawdown_pct": 99.0, "admissible": False}
    out = ss.search(measure, n=2, max_total=30.0)
    assert not out["ok"] and out["reason"] == "nothing admissible"
    assert out["coverage"]["coarse_points"] > 0


def test_the_search_reports_what_it_actually_tried():
    out = ss.search(_peak_at((30.0, 30.0)), n=2, max_total=97.0)
    cov = out["coverage"]
    assert cov["coarse_points"] > 0
    assert cov["measured_points"] >= cov["coarse_points"]
    assert cov["step_pct"] == ss.COARSE_STEP_PCT


def test_a_flat_landscape_terminates(monkeypatch=None):
    """Every point identical: refinement must stop rather than walk ties forever."""
    def measure(p):
        return {"excess_pct": 7.0, "max_drawdown_pct": 1.0, "admissible": True}
    out = ss.search(measure, n=2, max_total=40.0)
    assert out["ok"]
    assert out["spread"]["best_minus_median_pct"] == 0.0
    assert out["spread"]["is_noise"] is True


# --------------------------------------------------------------- the doubt
def test_an_optimum_indistinguishable_from_the_median_is_reported_as_noise():
    """If best and median differ by less than the noise floor, the 'optimum' is
    a coin landing and the card must not present it as a decision."""
    sp = ss.spread([10.0, 10.05, 10.1, 10.12, 10.15])
    assert sp["best_minus_median_pct"] < ss.NOISE_FLOOR_PCT
    assert sp["is_noise"] is True


def test_a_real_difference_between_splits_is_not_called_noise():
    sp = ss.spread([4.0, 8.0, 12.0, 16.0, 20.0])
    assert sp["best_pct"] == 20.0 and sp["median_pct"] == 12.0
    assert sp["best_minus_median_pct"] == 8.0 and sp["is_noise"] is False


def test_spread_of_nothing_is_empty_not_zero():
    sp = ss.spread([])
    assert sp["n"] == 0 and sp["best_pct"] is None and sp["is_noise"] is True


def test_rank_is_one_for_the_best():
    assert ss.rank_of(20.0, [4.0, 8.0, 20.0]) == 1
    assert ss.rank_of(8.0, [4.0, 8.0, 20.0]) == 2
    assert ss.rank_of(4.0, [4.0, 8.0, 20.0]) == 3


# ---------------------------------------------------- THE overfitting guard
def test_a_split_that_wins_in_sample_and_fails_out_is_called_failed():
    """The whole defence. A split chosen on 2019-2022 that lands near the bottom
    on 2023-2026 is a story about the first window, and the user is about to
    press Accept on it."""
    st = ss.stability(rank_out_of_sample=48, n_points=55)
    assert st["verdict"] == "failed"
    assert "will probably not repeat" in st["note"]


def test_a_split_that_holds_out_of_sample_is_called_held():
    st = ss.stability(rank_out_of_sample=3, n_points=55)
    assert st["verdict"] == "held"
    assert "data it was not chosen on" in st["note"]


def test_a_middling_split_is_called_weak_not_good():
    """The dangerous rounding is calling 'middling' a pass. It is not evidence
    of anything; it is the absence of evidence."""
    st = ss.stability(rank_out_of_sample=25, n_points=55)
    assert st["verdict"] == "weak"
    assert "unproven rather than measured" in st["note"]


def test_no_second_window_is_unknown_not_ok():
    st = ss.stability(rank_out_of_sample=0, n_points=0)
    assert st["verdict"] == "unknown" and st["ok"] is False
    assert "no evidence" in st["note"]


def test_the_decay_between_windows_is_reported_when_both_are_known():
    st = ss.stability(3, 55, in_sample_gain_pct=4.0, out_of_sample_gain_pct=0.5)
    assert st["decay_pct"] == 3.5


# ------------------------------------------------- comparison against T2
def test_a_gain_under_the_noise_floor_is_not_presented_as_a_win():
    c = ss.compare_to_ratio({"excess_pct": 15.20}, {"excess_pct": 15.17})
    assert c["is_noise"] is True
    assert "nothing here worth acting on" in c["note"]


def test_a_real_gain_over_the_current_ratio_is_stated_plainly():
    c = ss.compare_to_ratio({"excess_pct": 16.8}, {"excess_pct": 15.17})
    assert c["gain_pct"] == 1.63 and c["is_noise"] is False
    assert "+1.63%/yr" in c["note"]


def test_a_wider_search_that_found_something_worse_still_reports_honestly():
    """It can happen: T2's point is on a grid this search coarsened past."""
    c = ss.compare_to_ratio({"excess_pct": 14.0}, {"excess_pct": 15.17})
    assert c["gain_pct"] == -1.17 and c["is_noise"] is False


def test_nothing_to_compare_against_is_refused_rather_than_assumed_zero():
    assert not ss.compare_to_ratio({"excess_pct": 16.8}, None)["ok"]
    assert not ss.compare_to_ratio({"excess_pct": 16.8}, {"excess_pct": None})["ok"]


# ------------------------------------------ ranking out-of-sample (the plan)
def _two_window(oos_map, is_map=None, ceiling=100.0):
    """measure() returning both windows, so ranking can be checked directly."""
    def measure(p):
        key = tuple(p)
        o = oos_map.get(key)
        if o is None:
            return None
        f = (is_map or {}).get(key, o)
        return {"excess_pct": f, "oos_excess_pct": o,
                "max_drawdown_pct": sum(p) * 0.5,
                "admissible": sum(p) * 0.5 <= ceiling}
    return measure


def test_the_winner_is_chosen_out_of_sample_not_in_sample():
    """The plan is explicit: rank on the OOS split, not the full-sample figure.
    Here (10,0) wins in-sample by a mile and loses out-of-sample -- exactly the
    blend that must NOT be picked."""
    oos = {(0.0, 0.0): 1.0, (10.0, 0.0): 2.0, (0.0, 10.0): 9.0, (10.0, 10.0): 3.0}
    ins = {(0.0, 0.0): 1.0, (10.0, 0.0): 40.0, (0.0, 10.0): 9.5, (10.0, 10.0): 3.0}
    out = ss.search(_two_window(oos, ins), n=2, max_total=20.0, refine_steps=())
    assert out["ranked_on"] == "oos_excess_pct"
    assert out["best"]["split_pct"] == [0.0, 10.0]      # not [10, 0]


def test_the_oos_caveat_travels_with_the_ranking():
    """'Out of sample' borrows a lot of authority. The window contains exactly
    one bear market and the payload has to say so."""
    oos = {(0.0, 0.0): 1.0, (10.0, 0.0): 2.0}
    out = ss.search(_two_window(oos), n=2, max_total=10.0, refine_steps=())
    assert "sample of one" in out["ranked_on_note"]
    assert "2022-01-01" in ss.OOS_CAVEAT


def test_falling_back_to_the_full_sample_is_declared_not_silent():
    """No OOS figure means the winner WAS chosen on the data it is scored on.
    That is sometimes unavoidable and never acceptable to leave unsaid."""
    def measure(p):
        return {"excess_pct": sum(p), "max_drawdown_pct": 1.0, "admissible": True}
    out = ss.search(measure, n=2, max_total=20.0, refine_steps=())
    assert out["ranked_on"] == "excess_pct"
    assert "chosen on the same data it is scored on" in out["ranked_on_note"]


def test_every_ranked_row_carries_its_fit_test_gap():
    oos = {(0.0, 0.0): 1.0, (10.0, 0.0): 2.0, (0.0, 10.0): 9.0, (10.0, 10.0): 3.0}
    ins = {(0.0, 0.0): 1.0, (10.0, 0.0): 40.0, (0.0, 10.0): 9.5, (10.0, 10.0): 3.0}
    out = ss.search(_two_window(oos, ins), n=2, max_total=20.0, refine_steps=())
    assert out["top"], "no ranked rows"
    for row in out["top"]:
        assert row["gap_pct"] is not None
    # The in-sample star is visible and its collapse is quantified.
    star = [r for r in out["top"] if r["split_pct"] == [10.0, 0.0]][0]
    assert star["gap_pct"] == 38.0


def test_a_winner_that_decays_worse_than_typical_is_called_out():
    oos = {(0.0, 0.0): 1.0, (10.0, 0.0): 5.0, (0.0, 10.0): 2.0}
    ins = {(0.0, 0.0): 1.0, (10.0, 0.0): 25.0, (0.0, 10.0): 2.2}
    out = ss.search(_two_window(oos, ins), n=2, max_total=10.0, refine_steps=())
    ft = out["fit_test"]
    assert ft["winner_decays_more_than_typical"] is True
    assert "learned from the fitting window" in ft["note"]


def test_a_winner_that_decays_normally_is_not_smeared():
    oos = {(0.0, 0.0): 1.0, (10.0, 0.0): 5.0, (0.0, 10.0): 2.0}
    ins = {(0.0, 0.0): 2.0, (10.0, 0.0): 6.0, (0.0, 10.0): 3.0}
    out = ss.search(_two_window(oos, ins), n=2, max_total=10.0, refine_steps=())
    assert out["fit_test"]["winner_decays_more_than_typical"] is False


def test_fit_test_abstains_when_there_is_no_in_sample_figure():
    def measure(p):
        return {"excess_pct": sum(p), "max_drawdown_pct": 1.0, "admissible": True}
    out = ss.search(measure, n=2, max_total=10.0, refine_steps=())
    # excess_pct IS the rank key here, so there is no second window to compare.
    assert out["fit_test"]["ok"] is False


def test_the_ranked_list_is_capped_and_ordered_best_first():
    oos = {(a, b): float(a + 2 * b) for a in (0.0, 10.0, 20.0) for b in (0.0, 10.0, 20.0)
           if a + b <= 40.0}
    out = ss.search(_two_window(oos), n=2, max_total=40.0, refine_steps=(), top_n=3)
    assert len(out["top"]) == 3
    vals = [r["oos_excess_pct"] for r in out["top"]]
    assert vals == sorted(vals, reverse=True)
