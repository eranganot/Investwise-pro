"""W1 -- walk-forward parameter sweeps, and the answer is mostly "no".

The spec calls these *"the only untested source of additional measured edge in
the family, and a bigger lever on excess than any sleeve size"*
(`BEAT_MARKET_TARGET_SOLVER_PLAN.md:936-938`). Measured across all six
sweepable strategies, **not one reaches FITTING_HELPS**:

    btm_trend_tqqq       INCONCLUSIVE           3/6  mean  +8.49
    btm_trend_soxl       FITTING_DOES_NOT_HELP  1/6  mean -21.58
    btm_vol_target_tqqq  INCONCLUSIVE           4/6  mean  +3.23
    btm_swing_dip        FITTING_DOES_NOT_HELP  3/6  mean  -0.11
    btm_swing_breakout   FITTING_DOES_NOT_HELP  3/6  mean  -6.62
    btm_factor_stack     NO_SWEEP               -- buy_hold, no parameter
    btm_dual_momentum    INCONCLUSIVE           4/6  mean  +5.35

**Three of those would have been reported as wins on the mean alone.**
`btm_trend_tqqq` averages +8.49 while winning only three folds of six -- one or
two folds carrying the whole figure. That is precisely why the bar has two
halves and why both are module constants set before any number was measured:
with six folds it is trivial to decide afterwards what counts as a win, and that
is how a sweep manufactures an edge.

The question this asks is not "which value wins" -- that always has an answer,
always the in-sample winner. It is **does fitting beat not fitting**, so each
fold tests the fitted value against the value already in the catalog: the
counterfactual that actually existed had nobody swept.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.services import walk_forward as wf  # noqa: E402


# ===========================================================================
# the geometry is in sessions, because the engine's floor is
# ===========================================================================

def test_the_folds_match_the_engines_own_minimum():
    """`run()` abstains under `_MIN_OBSERVATIONS`. A calendar year is ~252
    sessions, so a geometry expressed in years abstains on every fold - which is
    exactly what the first recommended geometry would have done."""
    from app.engines.strategy_backtest import _MIN_OBSERVATIONS

    assert wf.MIN_SESSIONS == _MIN_OBSERVATIONS
    assert wf.TEST_SESSIONS >= _MIN_OBSERVATIONS
    for f in wf.folds(2513):
        assert f["test_to"] - f["test_from"] >= _MIN_OBSERVATIONS
        assert f["fit_to"] - f["fit_from"] >= _MIN_OBSERVATIONS


def test_every_fit_window_is_the_same_length():
    """The whole reason to prefer a rolling window over an expanding one: it
    makes the folds comparable, so "the parameter is stable" is a claim the data
    can support."""
    lengths = {f["fit_to"] - f["fit_from"] for f in wf.folds(2513)}
    assert lengths == {wf.FIT_SESSIONS}


def test_only_whole_folds_are_returned():
    """A ragged final fold would be shorter than the rest and would break the
    comparability the fixed fit length exists for."""
    for n in (2513, 2000, 1016, 1015):
        for f in wf.folds(n):
            assert f["test_to"] <= n


def test_too_little_history_yields_no_folds_rather_than_a_short_one():
    assert wf.folds(wf.FIT_SESSIONS + wf.TEST_SESSIONS - 1) == []
    assert len(wf.folds(wf.FIT_SESSIONS + wf.TEST_SESSIONS)) == 1


def test_the_live_book_gives_six_folds():
    assert len(wf.folds(2513)) == 6


# ===========================================================================
# the bar, and why it has two halves
# ===========================================================================

def _run(diffs, *, fit_pick=lambda v: v == 20):
    """Drive walk_forward so each fold's fitted-minus-default equals diffs[i]."""
    fl = wf.folds(2513)
    assert len(fl) == len(diffs)
    idx = {f["fold"]: i for i, f in enumerate(fl)}

    def measure(value, fold, phase):
        if phase == "fit":
            return 1.0 if fit_pick(value) else 0.0
        return (10.0 + diffs[idx[fold["fold"]]]) if fit_pick(value) else 10.0

    return wf.walk_forward([5, 10, 15, 20, 25], 5, fl, measure)


def test_a_positive_mean_carried_by_two_folds_is_not_a_win():
    """THE btm_trend_tqqq CASE. +8.49 mean while winning 3 of 6 - one or two
    folds carrying the whole figure. Reported on the mean alone it reads as the
    strongest result in the table."""
    out = _run([+30.0, +25.0, -1.0, -1.0, -1.0, -1.0])
    assert out["verdict"]["mean_diff"] > 0
    assert out["verdict"]["kind"] == wf.INCONCLUSIVE


def test_winning_every_fold_by_a_hair_is_not_a_win_either():
    """The other half of the bar. Six of six, mean +0.05 - under the noise
    floor the other three searches use."""
    out = _run([0.05] * 6)
    assert out["verdict"]["folds_won"] == 6
    assert out["verdict"]["kind"] == wf.INCONCLUSIVE


def test_both_halves_clearing_is_a_win():
    out = _run([2.0] * 6)
    assert out["verdict"]["kind"] == wf.FITTING_HELPS
    assert "6 of 6" in out["verdict"]["headline"]


def test_losing_on_average_is_a_finding_not_a_failure():
    """"Sweeping this overlay is not a source of edge" is a real answer. A
    report that can only conclude "parameter X won" will always find an edge."""
    out = _run([-5.0, -5.0, +1.0, -5.0, -5.0, +1.0])
    assert out["verdict"]["kind"] == wf.FITTING_DOES_NOT_HELP
    assert "is not a source of edge" in out["verdict"]["means"]


def test_the_bar_is_on_the_payload_so_it_can_be_checked():
    """Set before the numbers were seen, and stated where a reader can see it
    was not chosen to fit them."""
    out = _run([1.0] * 6)
    assert out["bar"]["min_win_fraction"] == wf.MIN_WIN_FRACTION
    assert out["bar"]["noise_floor"] == wf.NOISE_FLOOR_PCT
    assert "before the numbers" in out["bar"]["note"]


def test_the_noise_floor_agrees_with_the_other_searches():
    from app.services import core_search as cs
    from app.services import sleeve_count as sc
    from app.services import split_solver as sp

    assert wf.NOISE_FLOOR_PCT == cs.NOISE_FLOOR_PCT == sc.NOISE_FLOOR_PCT == sp.NOISE_FLOOR_PCT


# ===========================================================================
# the baseline is the value you actually run
# ===========================================================================

def test_the_default_is_tested_on_every_fold_not_just_the_fitted_value():
    """Measuring only the fitted value answers "how did the winner do", which is
    not the question."""
    seen = []
    fl = wf.folds(2513)

    def measure(value, fold, phase):
        if phase == "test":
            seen.append((fold["fold"], value))
        return 1.0 if value == 20 else 0.0

    wf.walk_forward([5, 10, 15, 20, 25], 5, fl, measure)
    for f in fl:
        assert (f["fold"], 20) in seen, "the fitted value was not tested"
        assert (f["fold"], 5) in seen, "the default was not tested"


def test_the_baseline_is_the_catalog_default_not_the_median_of_the_grid():
    """The default is the counterfactual that actually existed. The median is a
    value nobody was running."""
    out = _run([1.0] * 6)
    assert out["default_value"] == 5          # not 15, the grid's median


# ===========================================================================
# a jumping parameter has not been measured
# ===========================================================================

def test_stability_reports_how_often_the_fit_agreed_with_itself():
    """A parameter that jumps every fold has been resampled, not measured - and
    a report giving only the mean difference would hide that completely."""
    fl = wf.folds(2513)
    order = [5, 10, 15, 20, 25, 5]

    def measure(value, fold, phase):
        if phase == "fit":
            return 1.0 if value == order[fold["fold"] - 1] else 0.0
        return 1.0

    out = wf.walk_forward([5, 10, 15, 20, 25], 5, fl, measure)
    assert out["stability"]["distinct_values"] == 5
    assert out["stability"]["modal_folds"] == 2


# ===========================================================================
# nothing vanishes
# ===========================================================================

def test_an_abstaining_value_is_recorded_with_its_fold_and_phase():
    fl = wf.folds(2513)

    def measure(value, fold, phase):
        if value == 25:
            return None
        return 1.0 if value == 20 else 0.0

    out = wf.walk_forward([5, 10, 15, 20, 25], 5, fl, measure)
    assert out["abstained"], "a value that could not be measured must be named"
    assert all(a["value"] == 25 for a in out["abstained"])
    assert {a["fold"] for a in out["abstained"]} == {f["fold"] for f in fl}


def test_a_fold_where_nothing_fits_still_appears():
    fl = wf.folds(2513)
    out = wf.walk_forward([5], 5, fl, lambda v, f, p: None)
    assert len(out["folds"]) == len(fl)
    assert out["verdict"]["kind"] == wf.NOTHING_MEASURABLE


def test_the_untested_recent_remainder_is_named(monkeypatch):
    """2,513 does not divide into whole folds and the leftover sits at the
    RECENT end - the months a reader cares most about are the untested ones."""
    out = _run([1.0] * 6)
    assert out["coverage"]["tested_through"] == 2316


def test_a_strategy_with_no_sweep_abstains_by_name():
    """`btm_factor_stack` is buy_hold and correctly has no parameter. A strategy
    that silently vanished from a report would read as one that was measured and
    found wanting."""
    from app.services import backtest_service as bs
    from app.services import strategy_catalog

    spec = next(s for s in strategy_catalog.backtestable() if s["id"] == "btm_factor_stack")
    out = bs.walk_forward_blocking(spec)
    assert out["ok"] is False
    assert out["reason"] == "NO_SWEEP"
    assert "btm_factor_stack" in out["detail"]


@pytest.mark.parametrize("sid", ["btm_trend_tqqq", "btm_trend_soxl", "btm_vol_target_tqqq",
                                 "btm_swing_dip", "btm_swing_breakout", "btm_dual_momentum"])
def test_every_other_catalog_strategy_declares_both_a_sweep_and_a_default(sid):
    """The sweep is useless without a default to compare against - there would
    be no "what you already run"."""
    from app.services import strategy_catalog

    spec = next(s for s in strategy_catalog.backtestable() if s["id"] == sid)
    overlay = spec["overlay"]
    param = overlay.get("sweep_param")
    assert param, sid
    assert overlay.get("sweep_values"), sid
    assert overlay.get(param) is not None, f"{sid} sweeps {param} but does not set it"


# ===========================================================================
# it never writes a parameter back
# ===========================================================================

def test_nothing_writes_a_fitted_parameter_into_a_spec():
    """`backtest_service` already sets the rule: a measured-better variant ships
    OFF until a human compares the numbers. A route that tuned a live strategy
    from one history would be the overfitting this module exists to detect."""
    import inspect

    from app.services import backtest_service as bs

    src = inspect.getsource(bs.walk_forward_blocking)
    # The trial spec is built per measurement and thrown away; nothing assigns
    # back into the caller's overlay.
    assert "trial = {**spec" in src
    assert "spec[\"overlay\"][param] =" not in src
    assert "overlay[param] =" not in src


def test_the_disclaimer_ships_with_the_payload():
    import inspect

    from app.services import backtest_service as bs

    assert "Not financial advice." in inspect.getsource(bs.walk_forward_blocking)
