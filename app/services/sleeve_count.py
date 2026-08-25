"""T6b, second half -- discover how many sleeves, don't pick a number.

The plan's bullet, verbatim:

    "Discover the sleeve count, don't cap it. Grow the number of concurrent
     sleeves while each addition improves the constrained objective; stop when
     it stops, and report where it stopped and by how much the last one helped.
     The count is then a measurement rather than a number someone picked. Keep
     a hard runtime ceiling as a backstop, and log it if the backstop is what
     stopped the search -- a limit that binds silently reads as a finding."

T2 resizes a FIXED set. T6a sizes each member of that fixed set on its own axis.
Neither can answer "should there be a third one at all", because both take the
membership as given -- and this book runs two sleeves for no measured reason.

**Greedy forward selection, and the honesty is in what it reports rather than in
the algorithm.** Greedy is not optimal: the best pair is not necessarily the
best single plus one, so a set this search rejects may be better than the set it
keeps. That is a real limitation and `method` says so on the payload instead of
leaving the word "discovered" to imply an exhaustive search. The alternative --
every subset of seven candidates -- is 127 sets times two simulations of ten
years of daily closes, which is not a better answer so much as the same answer
arrived at expensively.

**Every round is reported, including its rejects.** "It stopped at two" is not a
finding; "it stopped at two because the best third sleeve added 0.11% against a
0.25% noise floor, and here is what each candidate third would have added" is.
The first can only be trusted; the second can be checked.

**The stop reason is a first-class field**, because the four ways this search can
end mean completely different things:

    BELOW_NOISE_FLOOR    the search genuinely converged -- the finding
    NO_ADMISSIBLE_ADDITION  every addition breached the ceiling
    MAX_SLEEVES          the candidate pool ran out, not the improvement
    RUNTIME_BACKSTOP     a budget stopped it, and the answer is incomplete

The last two are NOT convergence, and a payload that reported only the count
would render them identically to the first.

Pure. Takes a `measure` callable rather than price data, so every rule here is
testable without a database, a provider or a simulation -- the same contract
`split_solver` and `core_search` use.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SLEEVE_COUNT_VERSION = "t6b2"

# Below this, the last addition is not a finding. Same constant and the same
# reasoning as `split_solver.NOISE_FLOOR_PCT` and `core_search.NOISE_FLOOR_PCT`,
# duplicated deliberately so the three searches may diverge; the tests assert
# they currently agree, which is the coupling that actually matters.
NOISE_FLOOR_PCT = 0.25

# Backstops, not preferences. Seven is the whole `backtestable()` pool today, so
# MAX_SLEEVES only ever binds if that pool grows -- and if it binds, the search
# stopped because it ran out of candidates, which is not convergence.
MAX_SLEEVES = 7
# Each measure is one simulation over ten years of daily closes. A greedy pass
# over seven candidates is at most 7+6+5+4+3+2+1 = 28; 60 leaves room for a
# larger pool without letting a runaway loop turn an explicit button into a
# ten-minute wait.
MAX_MEASURES = 60

BELOW_NOISE_FLOOR = "BELOW_NOISE_FLOOR"
NO_ADMISSIBLE_ADDITION = "NO_ADMISSIBLE_ADDITION"
# The set the book ALREADY runs breaches the ceiling. Distinct from
# NO_ADMISSIBLE_ADDITION and found by running this against the real book: every
# candidate third sleeve breached, so the search reported "no admissible
# addition" -- technically true and badly misleading, because a reader sees a
# count of 2 and concludes two is right. The measured truth was that the two
# already held blow the ceiling by 31 points, which makes "which third improves
# it" the wrong question rather than one with no answer.
BASELINE_BREACHES_CEILING = "BASELINE_BREACHES_CEILING"
MAX_SLEEVES_REACHED = "MAX_SLEEVES"
RUNTIME_BACKSTOP = "RUNTIME_BACKSTOP"
NOTHING_MEASURABLE = "NOTHING_MEASURABLE"


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _score(metrics: dict, max_drawdown_pct: float) -> tuple[float | None, dict]:
    """Constrained excess: the excess, or None when the ceiling is breached.

    CONSTRAINED, not raw. Ranking additions on return alone selects for exactly
    the sleeves that make a book unholdable -- and every sleeve in this family is
    a leveraged or concentrated instrument, so raw excess is maximised by
    whichever addition adds the most leverage. `target_solver`'s own docstring
    makes the same point about the ceiling constraining the ADMISSIBLE SET
    rather than being reported afterwards.
    """
    if not metrics.get("ok"):
        return None, {"ok": False, "reason": metrics.get("reason"),
                      "detail": metrics.get("detail")}
    dd = _f(metrics.get("max_drawdown_pct"))
    ex = _f(metrics.get("excess_cagr_pct"))
    row = {"ok": True, "excess_pct": ex, "max_drawdown_pct": dd,
           "cagr_pct": _f(metrics.get("cagr_pct")),
           "sessions": metrics.get("observations")}
    if dd is not None and abs(dd) > max_drawdown_pct:
        row["breaches_ceiling"] = True
        return None, row
    row["breaches_ceiling"] = False
    return (ex if ex is not None else None), row


def discover(candidates: list[dict], measure, *, max_drawdown_pct: float,
             start: list[str] | None = None,
             noise_floor_pct: float = NOISE_FLOOR_PCT,
             max_sleeves: int = MAX_SLEEVES,
             max_measures: int = MAX_MEASURES) -> dict:
    """Grow the sleeve set while each addition earns its place.

    `candidates` is `[{"id", "name"}]`. `measure(ids)` takes a TUPLE of sleeve
    ids and returns a `measure_blend`-shaped dict for that set. `start` is the
    set the book already runs, so "one more than you have" is the question
    actually being asked; passing None starts from nothing and answers "how many
    at all", which is a different question and is reported as such.
    """
    pool = {c["id"]: (c.get("name") or c["id"]) for c in (candidates or [])}
    chosen = [c for c in (start or []) if c in pool]
    measures = 0
    rounds: list[dict] = []

    def _measure(ids: tuple[str, ...]) -> dict:
        nonlocal measures
        measures += 1
        return measure(ids) or {}

    # The baseline the first addition has to beat. An empty start measures the
    # core alone, which is a legitimate baseline: "no sleeves" is a sleeve count.
    base_metrics = _measure(tuple(chosen))
    base_score, base_row = _score(base_metrics, max_drawdown_pct)
    if not base_row.get("ok"):
        return {
            "ok": False, "version": SLEEVE_COUNT_VERSION,
            "stopped_because": NOTHING_MEASURABLE,
            "detail": f"the starting set could not be measured: "
                      f"{base_row.get('reason')} {base_row.get('detail') or ''}".strip(),
            "chosen": list(chosen), "count": len(chosen), "rounds": [],
        }

    current_score = base_score
    stopped = None
    last_gain = None

    # Checked BEFORE the first round, because every gain against an inadmissible
    # baseline is None and the loop would otherwise fall through to
    # NO_ADMISSIBLE_ADDITION and report a count that means nothing.
    if base_row.get("breaches_ceiling"):
        stopped = BASELINE_BREACHES_CEILING

    while stopped is None:
        if len(chosen) >= max_sleeves or not (set(pool) - set(chosen)):
            stopped = MAX_SLEEVES_REACHED
            break
        if measures >= max_measures:
            stopped = RUNTIME_BACKSTOP
            break

        tried: list[dict] = []
        best_id, best_score, best_row = None, None, None
        for cid in sorted(set(pool) - set(chosen)):
            if measures >= max_measures:
                # Stop mid-round rather than half-measuring it: a round whose
                # candidates were not all tried cannot support "this was the
                # best addition", so it is not recorded as one.
                stopped = RUNTIME_BACKSTOP
                break
            score, row = _score(_measure(tuple(chosen) + (cid,)), max_drawdown_pct)
            tried.append({"id": cid, "name": pool[cid], **row,
                          "gain_pct": (None if score is None or current_score is None
                                       else round(score - current_score, 4))})
            if score is not None and (best_score is None or score > best_score):
                best_id, best_score, best_row = cid, score, row
        if stopped == RUNTIME_BACKSTOP:
            # The partial round is still reported -- it is what the backstop
            # interrupted, and hiding it would make the budget invisible.
            rounds.append({"round": len(rounds) + 1, "added": None,
                           "gain_pct": None, "partial": True, "tried": tried})
            break

        if best_id is None:
            stopped = NO_ADMISSIBLE_ADDITION
            rounds.append({"round": len(rounds) + 1, "added": None,
                           "gain_pct": None, "partial": False, "tried": tried})
            break

        gain = (None if current_score is None else round(best_score - current_score, 4))
        if gain is not None and gain < noise_floor_pct:
            stopped = BELOW_NOISE_FLOOR
            last_gain = gain
            # RECORDED, NOT DISCARDED. "It stopped at two" is not a finding;
            # "the best third added 0.11% against a 0.25% floor" is, and it is
            # the row that lets a reader disagree with the floor.
            rounds.append({"round": len(rounds) + 1, "added": None,
                           "best_rejected": best_id, "gain_pct": gain,
                           "partial": False, "tried": tried})
            break

        chosen.append(best_id)
        last_gain = gain
        rounds.append({"round": len(rounds) + 1, "added": best_id,
                       "name": pool[best_id], "gain_pct": gain,
                       "excess_pct": best_row.get("excess_pct"),
                       "max_drawdown_pct": best_row.get("max_drawdown_pct"),
                       "partial": False, "tried": tried})
        current_score = best_score

    if stopped == RUNTIME_BACKSTOP:
        # "A limit that binds silently reads as a finding." -- the plan.
        logger.warning("sleeve_count: the runtime backstop stopped the search at "
                       "%d measures and %d sleeve(s); the count is a floor, not "
                       "a result", measures, len(chosen))

    converged = stopped == BELOW_NOISE_FLOOR

    # The headline, for the same reason `core_search` grew one: a bare count
    # renders identically whether it is a finding or an artefact of a question
    # that could not be asked.
    base_dd = base_row.get("max_drawdown_pct")
    if stopped == BASELINE_BREACHES_CEILING:
        headline = (f"The {len(chosen)} sleeve(s) you already run measure "
                    f"{abs(base_dd):.1f}% against your {max_drawdown_pct:.1f}% ceiling"
                    if base_dd is not None else
                    "The sleeves you already run breach your ceiling")
        means = ("Adding a sleeve cannot repair that and removing one may not "
                 "either - this count is not a recommendation. Size or remove "
                 "what you hold, or change the ceiling, before asking how many.")
    elif stopped == NO_ADMISSIBLE_ADDITION:
        headline = f"No further sleeve fits under your {max_drawdown_pct:.1f}% ceiling"
        means = ("The set you hold is admissible and nothing available improves it "
                 "without breaching. This is a finding, not a budget limit.")
    elif stopped == BELOW_NOISE_FLOOR:
        headline = f"{len(chosen)} sleeve(s) earn their place"
        means = (f"The best next addition improved the constrained objective by "
                 f"{last_gain:.2f}%, under the {noise_floor_pct:.2f}% noise floor."
                 if last_gain is not None else
                 "The next addition did not clear the noise floor.")
    else:
        headline = f"The search stopped at {len(chosen)} sleeve(s) without converging"
        means = ("It ran out of candidates or of budget, so this count is a floor "
                 "rather than a result. Re-run with a larger budget.")

    return {
        "ok": True,
        "verdict": {"kind": stopped, "headline": headline, "means": means,
                    "baseline_breaches_ceiling": bool(base_row.get("breaches_ceiling")),
                    "baseline_max_drawdown_pct": base_dd},
        "version": SLEEVE_COUNT_VERSION,
        "chosen": list(chosen),
        "count": len(chosen),
        "started_from": list(start or []),
        "baseline": base_row,
        "stopped_because": stopped,
        # The distinction the count alone destroys.
        "converged": converged,
        "count_is_a_floor": stopped in (RUNTIME_BACKSTOP, MAX_SLEEVES_REACHED),
        "last_gain_pct": last_gain,
        "noise_floor_pct": noise_floor_pct,
        "rounds": rounds,
        # Greedy is not exhaustive and the payload says so rather than letting
        # "discovered" imply otherwise.
        "method": {
            "kind": "greedy_forward_selection",
            "caveat": ("greedy: each round adds the single best next sleeve, so a "
                       "set rejected here may still beat the set kept. An exhaustive "
                       "search over 7 candidates is 127 sets at two simulations each."),
            "ranked_on": "excess_cagr_pct, constrained to the drawdown ceiling",
        },
        "coverage": {
            "candidates": len(pool),
            "measures": measures,
            "max_measures": max_measures,
            "max_sleeves": max_sleeves,
            "runtime_backstop_hit": stopped == RUNTIME_BACKSTOP,
        },
    }
