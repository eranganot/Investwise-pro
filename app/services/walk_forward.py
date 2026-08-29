"""W1 -- does fitting the parameter beat not fitting it?

The plan's bullet:

    "Walk-forward parameter sweeps. `sweep_param` / `sweep_values` already exist
     on every overlay and have never been run walk-forward. The only untested
     source of *additional* measured edge in the family, and a bigger lever on
     excess than any sleeve size."

Two corrections the code made to that sentence, both recorded in
`PHASE_W_PLAN.md`:

  * They exist on **6 of 7** catalog entries. `btm_factor_stack` is `buy_hold`
    and correctly has no parameter to sweep.
  * They ARE run -- nightly, on the full sample, by `backtest_service.measure`
    -- and the result is stored in `StrategyBacktest.robustness` and **read by
    nothing**. So the claim is precisely "never run WALK-FORWARD", and the
    existing sweep neither splits nor selects.

**THE QUESTION THIS ASKS.** Not "which parameter value wins": that question
always has an answer, the answer is always the in-sample winner, and reporting
it is how a sweep manufactures an edge. The question is

    does fitting the parameter beat NOT fitting it?

so every fold measures two things out-of-sample -- the value **fitted** on the
data available at the time, and the value **already in the catalog**, which is
what would have run had nobody swept. The baseline is the catalog default rather
than the median of the grid because the default is the counterfactual that
actually existed.

If fitting earns nothing, that is the finding and `verdict` says so in words. A
report that can only conclude "parameter X won" is a report that will always
find an edge.

**THE BAR IS SET HERE, BEFORE ANY NUMBERS.** With six folds it would be trivial
to look at the results and then decide what counts as a win. `MIN_WIN_FRACTION`
and `NOISE_FLOOR_PCT` are module constants for that reason, and both must clear
for `FITTING_HELPS`: winning four folds by a hair is not the same claim as
winning five by a margin, and a mean difference alone can be carried by one fold.

**Pure.** Takes a `measure` callable rather than price data, so every rule here
is testable without a database, a provider or a simulation -- the same contract
`split_solver`, `core_search` and `sleeve_count` use.
"""
from __future__ import annotations

import logging
from collections import Counter

logger = logging.getLogger(__name__)

WALK_FORWARD_VERSION = "w1"

# The engine's floor, and the reason every number here is in SESSIONS rather
# than years. `strategy_backtest._MIN_OBSERVATIONS` is 260 with the comment
# "~1 trading year; below this, CAGR is noise", and `run()` abstains under it.
# A calendar year is ~252 sessions, so a geometry expressed in years abstains on
# every fold -- which is exactly what the first recommended geometry would have
# done. Not imported: it is private to the engine, and a public copy that drifts
# is worse than a stated duplicate. The tests assert the two agree.
MIN_SESSIONS = 260

# Rolling window, fixed fit length. The fixed length is the whole point: it makes
# the folds comparable to each other, so "the parameter is stable" is a claim the
# data can support. Under an expanding window the first fold chooses on 260
# sessions and the last on 2,080, and a value that changes between them says
# nothing about the parameter -- only about how much data produced each choice.
FIT_SESSIONS = 756          # ~3 trading years
TEST_SESSIONS = MIN_SESSIONS
STEP_SESSIONS = MIN_SESSIONS

# The bar for FITTING_HELPS, both halves required. Stated before the numbers.
MIN_WIN_FRACTION = 0.8      # 5 of 6 folds
NOISE_FLOOR_PCT = 0.25      # the same floor the other three searches use

FITTING_HELPS = "FITTING_HELPS"
FITTING_DOES_NOT_HELP = "FITTING_DOES_NOT_HELP"
INCONCLUSIVE = "INCONCLUSIVE"
NOTHING_MEASURABLE = "NOTHING_MEASURABLE"


def folds(n_sessions: int, *, fit: int = FIT_SESSIONS, test: int = TEST_SESSIONS,
          step: int = STEP_SESSIONS) -> list[dict]:
    """Rolling fit/test windows as SESSION INDEX ranges, never dates.

    Returns only whole folds. A ragged final fold would be shorter than the rest
    and would break the comparability the fixed fit length exists for, so the
    remainder is left out -- and `coverage` in `walk_forward` names it, because
    the leftover sits at the RECENT end and the months not tested are the ones a
    reader cares most about.
    """
    out: list[dict] = []
    start = 0
    while start + fit + test <= n_sessions:
        out.append({
            "fold": len(out) + 1,
            "fit_from": start, "fit_to": start + fit,
            "test_from": start + fit, "test_to": start + fit + test,
            "fit_sessions": fit, "test_sessions": test,
        })
        start += step
    return out


def _mean(xs: list[float]) -> float | None:
    return (sum(xs) / len(xs)) if xs else None


def walk_forward(values: list, default_value, fold_list: list[dict], measure) -> dict:
    """Fit a parameter per fold, then test it against the value already in use.

    `measure(value, fold, phase) -> float | None` with `phase` in
    `{"fit", "test"}`. `None` is an abstention -- recorded with its fold and
    value, never scored as zero, because a simulation that could not run is not
    a bad result.
    """
    if not values or not fold_list:
        return {"ok": False, "version": WALK_FORWARD_VERSION,
                "verdict": {"kind": NOTHING_MEASURABLE,
                            "headline": "nothing to walk forward",
                            "means": "no parameter values or no whole folds fit "
                                     "the history available."},
                "folds": [], "abstained": []}

    rows: list[dict] = []
    abstained: list[dict] = []

    for fold in fold_list:
        # 1. Fit: score every value on the fit window and take the best.
        fitted: list[tuple] = []
        for v in values:
            s = measure(v, fold, "fit")
            if s is None:
                abstained.append({"fold": fold["fold"], "value": v, "phase": "fit",
                                  "reason": "the simulation did not return"})
                continue
            fitted.append((v, float(s)))
        if not fitted:
            rows.append({**fold, "chosen": None, "fitted_test": None,
                         "default_test": None, "diff": None,
                         "note": "no value could be fitted on this window"})
            continue
        chosen = max(fitted, key=lambda p: p[1])[0]

        # 2. Test: the fitted value AND the one already in the catalog, on the
        #    SAME window. Measuring only the fitted value would answer "how did
        #    the winner do", which is not the question.
        f_test = measure(chosen, fold, "test")
        d_test = measure(default_value, fold, "test")
        for label, val, score in (("fitted", chosen, f_test),
                                  ("default", default_value, d_test)):
            if score is None:
                abstained.append({"fold": fold["fold"], "value": val,
                                  "phase": f"test/{label}",
                                  "reason": "the simulation did not return"})
        diff = (None if f_test is None or d_test is None
                else round(float(f_test) - float(d_test), 4))
        rows.append({**fold, "chosen": chosen,
                     "fit_best_score": round(max(s for _, s in fitted), 4),
                     "fitted_test": None if f_test is None else round(float(f_test), 4),
                     "default_test": None if d_test is None else round(float(d_test), 4),
                     "diff": diff,
                     "fitted_won": None if diff is None else diff > 0})

    scored = [r for r in rows if r["diff"] is not None]
    won = [r for r in scored if r["diff"] > 0]
    mean_diff = _mean([r["diff"] for r in scored])

    # THE VERDICT. Both halves of the bar, and the bar was set above.
    if not scored:
        kind = NOTHING_MEASURABLE
        headline = "No fold produced a comparable pair."
        means = ("Every fold abstained on one side or the other, so fitting was "
                 "never actually compared against not fitting.")
    else:
        enough_wins = len(won) >= MIN_WIN_FRACTION * len(scored)
        big_enough = mean_diff is not None and mean_diff >= NOISE_FLOOR_PCT
        if enough_wins and big_enough:
            kind = FITTING_HELPS
            headline = (f"Fitting beat the value you already run in {len(won)} of "
                        f"{len(scored)} folds, by {mean_diff:+.2f} on average.")
            means = ("Both halves of the bar cleared: it won often enough, and by "
                     "more than the noise floor. Worth acting on, and worth "
                     "re-running before you do.")
        elif mean_diff is not None and mean_diff <= 0:
            kind = FITTING_DOES_NOT_HELP
            headline = (f"Fitting LOST to the value you already run, by "
                        f"{mean_diff:+.2f} on average across {len(scored)} folds.")
            means = ("The parameter you have is as good as one chosen from the "
                     "data, or better. Sweeping this overlay is not a source of "
                     "edge, and that is a real answer rather than a failed search.")
        else:
            kind = INCONCLUSIVE
            headline = (f"Fitting won {len(won)} of {len(scored)} folds, mean "
                        f"{mean_diff:+.2f} -- under the bar.")
            means = (f"The bar was {MIN_WIN_FRACTION:.0%} of folds AND a mean of "
                     f"{NOISE_FLOOR_PCT:.2f}, set before these numbers existed. "
                     f"Missing it is not evidence fitting fails; it is the absence "
                     f"of evidence that it works.")

    # HOW OFTEN DID THE FIT PICK THE SAME VALUE? A parameter that jumps every
    # fold has not been measured, it has been resampled -- and a search that
    # reported only the mean difference would hide that completely.
    picks = [r["chosen"] for r in rows if r.get("chosen") is not None]
    counts = Counter(picks)
    modal, modal_n = (counts.most_common(1)[0] if counts else (None, 0))
    stability = {
        "distinct_values": len(counts),
        "modal_value": modal,
        "modal_folds": modal_n,
        "of_folds": len(picks),
        "note": (f"the fit chose {modal!r} in {modal_n} of {len(picks)} folds"
                 if counts else "no fold produced a fitted value"),
    }

    tested = sum(r["test_to"] - r["test_from"] for r in rows)
    span = max((r["test_to"] for r in rows), default=0)

    return {
        "ok": True,
        "version": WALK_FORWARD_VERSION,
        "verdict": {"kind": kind, "headline": headline, "means": means,
                    "folds_won": len(won), "folds_scored": len(scored),
                    "mean_diff": None if mean_diff is None else round(mean_diff, 4)},
        "default_value": default_value,
        "values": list(values),
        "geometry": {"fit_sessions": FIT_SESSIONS, "test_sessions": TEST_SESSIONS,
                     "step_sessions": STEP_SESSIONS, "folds": len(fold_list),
                     "kind": "rolling_fixed_fit"},
        "folds": rows,
        "stability": stability,
        "abstained": abstained,
        "coverage": {"sessions_tested": tested, "tested_through": span},
        "bar": {"min_win_fraction": MIN_WIN_FRACTION,
                "noise_floor": NOISE_FLOOR_PCT,
                "note": ("set before the numbers were seen. With six folds it "
                         "would be trivial to decide afterwards what counts as a "
                         "win, which is how a sweep manufactures an edge.")},
        "caveat": (f"every test window is exactly {TEST_SESSIONS} sessions, which is "
                   f"the engine's own minimum -- its comment for that floor is "
                   f"'below this, CAGR is noise'. So each fold's out-of-sample "
                   f"figure is the noisiest measurement this engine will produce, "
                   f"and the verdict rests on agreement across folds rather than "
                   f"on any one of them."),
    }
