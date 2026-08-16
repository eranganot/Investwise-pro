"""T6 - size each sleeve on its own axis, and say how much to trust the answer.

T2 sweeps ONE axis: total sleeve share, divided at the ratio the book already
runs. Its own docstring names the gap -- *"Sizing each independently is a wider
search, not this one. That search is T6."* This is that search.

**The reason this phase is dangerous is not the compute.** Picking the best of N
splits measured over the SAME ten years the answer is scored on is textbook
in-sample optimisation. Search hard enough over one history and something always
wins; that it won says almost nothing about whether it will win again. Every
mechanism in this module exists because the output of the search ends at a green
Accept button, and a number that arrived by curve-fitting looks exactly like a
number that arrived by insight.

So the search reports four things, and three of them are about doubt:

  best        the winning split, and what it measured
  spread      best vs MEDIAN vs worst across everything searched. If the best is
              +16.8% and the median is +16.4%, the choice barely matters and the
              "optimum" is noise dressed as a decision.
  coverage    how many points, on what grid, and whether the grid was coarsened
              to fit a budget -- so "best" is always read as "best of what was
              actually tried", never as "best possible".
  fit_test    THE one that matters. Every candidate carries what it measured
              IN-SAMPLE against what it measured OUT-OF-SAMPLE, and the winner's
              gap is shown beside the median gap. A split that wins before
              2022-01-01 and gives it all back after is the most useful row on
              the screen, and the card has to show it before the user presses
              Accept. Ranking itself is done on the out-of-sample figure, using
              `backtest_service.OOS_SPLIT` -- the constant that already exists --
              never on the full sample the winner was chosen from.

The search driver takes a `measure` callable rather than price data, so every
rule in here is testable without a database, a provider or a simulation.
"""
from __future__ import annotations

import logging
from itertools import product

logger = logging.getLogger(__name__)

SPLIT_VERSION = "t6a"

# The plan is explicit: rank on the out-of-sample split `backtest_service`
# ALREADY computes, not on the full-sample figure -- and carry that constant's
# own comment onto the card. It is imported rather than restated so the two can
# never drift into disagreeing about which window is out of sample.
#
#     OOS_SPLIT = "2022-01-01"   # the only real bear market these instruments
#                                # have seen
#
# That comment is the point. An out-of-sample window containing exactly one bear
# market is a sample of ONE, and a ranking built on it has to say so rather than
# borrowing the authority of the phrase "out of sample".
OOS_CAVEAT = ("ranked out-of-sample from 2022-01-01 -- the only real bear market "
              "these instruments have seen. One bear market is a sample of one.")

# The key the search ranks on. Falls back to full-sample only when no
# out-of-sample figure exists, and SAYS SO in the payload when it does.
RANK_KEY = "oos_excess_pct"
FALLBACK_RANK_KEY = "excess_pct"

# The coarse grid. 10 points keeps a 2-sleeve book at ~55 combinations and a
# 3-sleeve book at ~220 -- each one a full simulation over ten years of closes,
# so this is the number that decides whether a solve takes seconds or minutes.
COARSE_STEP_PCT = 10.0
# Refinement passes around the coarse winner. Local only: a full 1-point simplex
# is ~4,700 points for two sleeves, which is not a better answer, it is the same
# answer arrived at by measuring noise 4,700 times.
REFINE_STEPS = (5.0, 1.0)
MAX_REFINE_ROUNDS = 6
# Hard ceiling on coarse points. Above this the grid is coarsened and SAID to be
# coarsened, rather than the solve quietly taking ten minutes.
MAX_COARSE_POINTS = 400

# Below this, "the best split" is not a finding. Reported, not silently ignored.
NOISE_FLOOR_PCT = 0.25


# --------------------------------------------------------------------------
# the geometry -- pure, no measurement
# --------------------------------------------------------------------------

def simplex_grid(n: int, step: float, max_total: float,
                 *, max_points: int = MAX_COARSE_POINTS) -> tuple[list[tuple], float]:
    """Every n-tuple on a `step` grid whose entries sum to <= `max_total`.

    Returns `(points, step_actually_used)`. The step is COARSENED rather than the
    point list truncated: truncating would silently search a corner of the space
    and report the winner as though the whole space had been tried, which is the
    failure this module's `coverage` block exists to make impossible.
    """
    if n <= 0 or step <= 0 or max_total <= 0:
        return [], step
    used = float(step)
    for _ in range(8):
        levels = [round(i * used, 4) for i in range(int(max_total // used) + 1)]
        if len(levels) ** n > 200_000:      # do not even build it
            used *= 2
            continue
        pts = [p for p in product(levels, repeat=n)
               if round(sum(p), 4) <= max_total + 1e-9]
        if len(pts) <= max_points:
            return sorted(pts), used
        used *= 2
    return sorted(pts[:max_points]), used


def neighbours(point: tuple, step: float, max_total: float) -> list[tuple]:
    """Points one `step` away: each axis alone, and each pair moved oppositely.

    The paired moves are what let the search slide along a constant total --
    trading size between two sleeves without changing how much of the book is
    sleeved. Axis-only moves cannot do that, and "the same 65%, divided
    differently" is precisely the question this phase was asked.
    """
    n = len(point)
    out = set()
    for i in range(n):
        for d in (-step, step):
            q = list(point)
            q[i] = round(q[i] + d, 4)
            if q[i] < -1e-9 or round(sum(q), 4) > max_total + 1e-9:
                continue
            out.add(tuple(q))
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            q = list(point)
            q[i] = round(q[i] + step, 4)
            q[j] = round(q[j] - step, 4)
            if min(q) < -1e-9 or round(sum(q), 4) > max_total + 1e-9:
                continue
            out.add(tuple(q))
    out.discard(tuple(point))
    return sorted(out)


# --------------------------------------------------------------------------
# reading the results honestly
# --------------------------------------------------------------------------

def spread(values: list[float]) -> dict:
    """Best, median and worst across everything searched.

    The gap between best and MEDIAN is the number that decides whether this
    phase found anything. A 0.1-point gap means every split does about the same
    and the "optimum" is a coin landing; a 6-point gap means the split genuinely
    matters -- and also that there is more room for the winner to be luck.
    Either way the user should see it beside the answer, not instead of it.
    """
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {"n": 0, "best_pct": None, "median_pct": None, "worst_pct": None,
                "best_minus_median_pct": None, "is_noise": True}
    mid = len(vals) // 2
    median = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0
    best = vals[-1]
    return {"n": len(vals),
            "best_pct": round(best, 2),
            "median_pct": round(median, 2),
            "worst_pct": round(vals[0], 2),
            "best_minus_median_pct": round(best - median, 2),
            "is_noise": (best - median) < NOISE_FLOOR_PCT}


def rank_of(value: float, values: list[float]) -> int:
    """1 = best. Higher excess ranks better."""
    vals = [v for v in values if v is not None]
    if value is None or not vals:
        return 0
    return 1 + sum(1 for v in vals if v > value + 1e-12)


def stability(rank_out_of_sample: int, n_points: int,
              *, in_sample_gain_pct: float | None = None,
              out_of_sample_gain_pct: float | None = None) -> dict:
    """Did the split that won the first half still work in the second?

    This is the whole defence against shipping a curve-fit. The search is run on
    the FIRST half of the window alone; its winner is then ranked among the same
    candidates scored on the SECOND half. A winner that stays near the top held
    up on data it was not chosen on. A winner that lands mid-pack was chosen by
    the first half's particular accidents.

    Deliberately NOT expressed as a pass/fail. It is a percentile plus a plain
    sentence, because the user is the one pressing Accept and "trust me" is not
    a thing this app gets to say.
    """
    if not n_points or not rank_out_of_sample:
        return {"ok": False, "reason": "not enough data to check",
                "verdict": "unknown",
                "note": ("the split could not be re-tested on a second window, so "
                         "there is no evidence it holds up out of sample")}
    pct = round(100.0 * rank_out_of_sample / n_points, 1)
    if pct <= 25:
        verdict, note = "held", (
            f"the split chosen on the first half ranked {rank_out_of_sample} of "
            f"{n_points} on the second half (top {pct}%). It kept working on data "
            f"it was not chosen on.")
    elif pct <= 60:
        verdict, note = "weak", (
            f"the split chosen on the first half ranked only {rank_out_of_sample} "
            f"of {n_points} on the second half (top {pct}%). Middling. Treat the "
            f"improvement as unproven rather than measured.")
    else:
        verdict, note = "failed", (
            f"the split chosen on the first half ranked {rank_out_of_sample} of "
            f"{n_points} on the second half (bottom {round(100 - pct, 1)}%). This "
            f"split is a story about the first window, not a property of the "
            f"strategies. The gain will probably not repeat.")
    out = {"ok": True, "rank": rank_out_of_sample, "of": n_points,
           "percentile": pct, "verdict": verdict, "note": note}
    if in_sample_gain_pct is not None and out_of_sample_gain_pct is not None:
        out["in_sample_gain_pct"] = round(in_sample_gain_pct, 2)
        out["out_of_sample_gain_pct"] = round(out_of_sample_gain_pct, 2)
        out["decay_pct"] = round(in_sample_gain_pct - out_of_sample_gain_pct, 2)
    return out


def fit_test(items: list, rank_key: str) -> dict:
    """The winner's in-sample vs out-of-sample gap, and how typical it is.

    The plan calls a blend that wins in-sample and collapses out-of-sample "the
    most useful row on the screen", and it is: a large positive gap means the
    split was learned from the fitting window rather than found in the
    strategies. The winner's gap alone is not enough though -- if EVERY
    candidate shows the same decay, that is the instruments, not this split. So
    the median gap is reported beside it.
    """
    gaps = []
    for _, m in items:
        f, o = m.get("excess_pct"), m.get(RANK_KEY)
        if f is not None and o is not None:
            gaps.append(f - o)
    if not gaps:
        return {"ok": False, "reason": "no in-sample figure to compare against"}
    best_m = max(items, key=lambda kv: kv[1][rank_key])[1]
    bf, bo = best_m.get("excess_pct"), best_m.get(RANK_KEY)
    srt = sorted(gaps)
    mid = len(srt) // 2
    median_gap = srt[mid] if len(srt) % 2 else (srt[mid - 1] + srt[mid]) / 2.0
    winner_gap = (bf - bo) if (bf is not None and bo is not None) else None
    return {"ok": True,
            "winner_in_sample_pct": round(bf, 2) if bf is not None else None,
            "winner_out_of_sample_pct": round(bo, 2) if bo is not None else None,
            "winner_gap_pct": round(winner_gap, 2) if winner_gap is not None else None,
            "median_gap_pct": round(median_gap, 2),
            "winner_decays_more_than_typical": (
                winner_gap is not None and winner_gap > median_gap + NOISE_FLOOR_PCT),
            "note": ("the winner gives up more between windows than the typical "
                     "candidate -- a sign the split was learned from the fitting "
                     "window rather than found in the strategies"
                     if (winner_gap is not None and winner_gap > median_gap + NOISE_FLOOR_PCT)
                     else "the winner decays no worse than the typical candidate")}


# --------------------------------------------------------------------------
# the search driver
# --------------------------------------------------------------------------

def search(measure, *, n: int, max_total: float,
           step: float = COARSE_STEP_PCT,
           refine_steps: tuple = REFINE_STEPS,
           max_points: int = MAX_COARSE_POINTS,
           top_n: int = 8) -> dict:
    """Coarse simplex sweep, then a local pattern search around the winner.

    `measure(point) -> {"excess_pct": float, "admissible": bool, ...} | None`
    where `point` is an n-tuple of sleeve percentages. `None` means the blend
    could not be measured at that point; it is recorded as unmeasurable rather
    than scored as zero, because a failed simulation is not a bad result.

    Returns the winner, everything scored, and the honesty blocks. NEVER claims a
    global optimum: refinement is local, so `best` is the best point REACHED, and
    `coverage.method` says so in the payload rather than in a comment here.
    """
    coarse, used_step = simplex_grid(n, step, max_total, max_points=max_points)
    if not coarse:
        return {"ok": False, "reason": "no admissible split grid",
                "detail": f"n={n}, max_total={max_total}"}

    scored: dict[tuple, dict] = {}
    unmeasurable = 0

    def score(p):
        nonlocal unmeasurable
        p = tuple(round(x, 4) for x in p)
        if p in scored:
            return scored[p]
        m = measure(p)
        if m is None:
            unmeasurable += 1
            scored[p] = None
            return None
        scored[p] = m
        return m

    for p in coarse:
        score(p)

    # Which figure decides the winner. Determined ONCE from the first measured
    # point, not per point -- ranking some candidates on out-of-sample and others
    # on full-sample would silently compare two different questions.
    probe = next((m for m in scored.values() if m), None)
    rank_key = (RANK_KEY if probe and probe.get(RANK_KEY) is not None
                else FALLBACK_RANK_KEY)

    def admissible_items():
        return [(p, m) for p, m in scored.items()
                if m and m.get("admissible") and m.get(rank_key) is not None]

    items = admissible_items()
    if not items:
        return {"ok": False, "reason": "nothing admissible",
                "detail": (f"every one of the {len(coarse)} splits tried breached the "
                           f"drawdown ceiling or a cap"),
                "coverage": {"coarse_points": len(coarse), "step_pct": used_step,
                             "unmeasurable": unmeasurable},
                "split_version": SPLIT_VERSION}

    best_p, best_m = max(items, key=lambda kv: kv[1][rank_key])
    coarse_best_p, coarse_best_m = best_p, best_m

    # Local refinement. Bounded by MAX_REFINE_ROUNDS per step so a flat landscape
    # cannot walk forever across ties.
    for fine in refine_steps:
        if fine >= used_step:
            continue
        for _ in range(MAX_REFINE_ROUNDS):
            moved = False
            for q in neighbours(best_p, fine, max_total):
                m = score(q)
                if (m and m.get("admissible") and m.get(rank_key) is not None
                        and m[rank_key] > best_m[rank_key] + 1e-9):
                    best_p, best_m, moved = q, m, True
            if not moved:
                break

    items = admissible_items()
    excesses = [m[rank_key] for _, m in items]
    return {
        "ok": True,
        "best": {"split_pct": list(best_p),
                 "total_pct": round(sum(best_p), 2),
                 **{k: v for k, v in best_m.items() if k != "admissible"}},
        "coarse_best": {"split_pct": list(coarse_best_p),
                        "excess_pct": coarse_best_m.get(rank_key)},
        "refinement_moved": tuple(round(x, 4) for x in best_p) != tuple(round(x, 4) for x in coarse_best_p),
        "spread": spread(excesses),
        "coverage": {
            "coarse_points": len(coarse),
            "step_pct": used_step,
            "step_was_coarsened": used_step > step,
            "measured_points": len(scored),
            "admissible_points": len(items),
            "unmeasurable": unmeasurable,
            # Said in the PAYLOAD, not only in a comment, because this is the
            # sentence that keeps "best" from being read as "best possible".
            "method": ("coarse simplex sweep then local pattern search; the result "
                       "is the best point REACHED, not a proven global optimum"),
        },
        "ranked_on": rank_key,
        "ranked_on_note": (OOS_CAVEAT if rank_key == RANK_KEY else
                           "ranked on the FULL sample -- no out-of-sample figure was "
                           "available, so this winner was chosen on the same data it "
                           "is scored on"),
        # THE row the plan calls the most useful on the screen: what each
        # candidate measured in-sample versus out. A blend that wins in-sample
        # and collapses out-of-sample is visible here and nowhere else.
        "fit_test": fit_test(items, rank_key),
        "top": [{"split_pct": list(p), "total_pct": round(sum(p), 2),
                 "oos_excess_pct": m.get(RANK_KEY),
                 "excess_pct": m.get("excess_pct"),
                 "gap_pct": (round(m["excess_pct"] - m[RANK_KEY], 2)
                             if m.get("excess_pct") is not None
                             and m.get(RANK_KEY) is not None else None),
                 "max_drawdown_pct": m.get("max_drawdown_pct")}
                for p, m in sorted(items, key=lambda kv: -kv[1][rank_key])[:top_n]],
        "scored": {",".join(f"{x:g}" for x in p): m[rank_key]
                   for p, m in items},
        "split_version": SPLIT_VERSION,
    }


def compare_to_ratio(best: dict, at_ratio: dict | None) -> dict:
    """What the wider search bought over T2's answer, in one honest line.

    A gain under the noise floor is reported as "not a finding" rather than as a
    small win: shipping "+0.08%/yr, optimised" invites the user to act on the
    difference between two indistinguishable numbers.
    """
    if not at_ratio or at_ratio.get("excess_pct") is None or not best:
        return {"ok": False, "reason": "nothing to compare against"}
    gain = float(best.get("excess_pct") or 0.0) - float(at_ratio["excess_pct"])
    return {"ok": True,
            "gain_pct": round(gain, 2),
            "at_ratio_excess_pct": round(float(at_ratio["excess_pct"]), 2),
            "best_excess_pct": round(float(best.get("excess_pct") or 0.0), 2),
            "is_noise": abs(gain) < NOISE_FLOOR_PCT,
            "note": ("below the noise floor -- the two splits measured the same, so "
                     "there is nothing here worth acting on"
                     if abs(gain) < NOISE_FLOOR_PCT else
                     f"the wider search found {gain:+.2f}%/yr over the ratio you run")}
