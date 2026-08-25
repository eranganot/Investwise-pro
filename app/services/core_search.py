"""T6b - vary the core, and never let the ranking hide the other window.

T2 sweeps ONE axis and holds the core FIXED: `core_pct = max_total - total_pct`,
so the core is whatever the sleeves have not claimed, composed of whatever the
book happens to hold. `_components()` treats it as an ordinary component and
`measure_blend` has never special-cased `__core__`, so making the core a
VARIABLE is a change to the search, not to the simulator.

The plan's line for this phase:

    "Vary the core -- each static family in `services/strategies.CATALOG` as the
     core component, plus current holdings as the baseline core."

**The window this ranks on was decided against the spec's recommendation, with
the measurement in hand.** The spec's table says the 250-day window has "no bear
market". Measured 2026-08-25 over 2025-08-27..2026-08-25, that is false:

    SPY  10y -34.10%   last-250  -9.13%
    TQQQ 10y -81.75%   last-250 -37.17%
    SOXL 10y -90.51%   last-250 -69.42%
    BND  10y -23.96%   last-250  -4.02%

SOXL fell 69% inside the short window, so for a book whose sleeves are TQQQ and
SOXL the recent window prices the binding risk properly, and that is the window
the ranking uses.

**BND is why every row also carries the ten-year.** -4.02% against -23.96% is a
six-fold understatement, and the 2022 rate shock is the whole risk story for the
four bond-heavy families -- which are exactly the candidates a drawdown ceiling
pushes a reader toward. A ranking on the short window alone would flatter
precisely the candidates it selects. So:

  BOTH WINDOWS ARE MEASURED FOR EVERY CANDIDATE. Not the winner, not the top
  three -- every one, so the comparison exists before anything is sorted.

  A CANDIDATE THAT PASSES THE CEILING ON ONE WINDOW AND BREACHES IT ON THE OTHER
  IS FLAGGED, and the flag travels in the payload rather than being left for a
  renderer to derive. `ceiling_disagreement` is a top-level list precisely so a
  card cannot draw the ranking without having been handed the contradiction.

  THE PAYLOAD SAYS WHICH WINDOW SORTED IT. `ranked_on` is not decoration. A
  ranked list whose window is implicit is a list that will be read as "best",
  and "best over one year" and "best" are different claims.

**The line this phase must not cross** (investing-discipline SS5): the app is a
self-directed execution companion and never recommends products. Candidates come
from `strategies.CATALOG` and nowhere else; the moment the search could pick a
ticker, it would be recommending a product. The wording that goes on the card is
"these measured highest over this window", never "you should hold" -- and there
is no execution path from here at all: `target_solver` is read-only without
exception, and swapping a core is a whole-book change.

**The overfitting problem is worse here than in T6a**, not better: fourteen
candidates times a split search is a far wider net over the same history. So the
spread block is mandatory rather than decorative -- if the best and the median
are within `NOISE_FLOOR_PCT`, the "winner" is noise wearing an ordinal, and the
payload says so in a field rather than leaving it to be noticed.

Pure. Takes `measure` callables rather than price data, so every rule in here is
testable without a database, a provider or a simulation -- the same contract
`split_solver` uses.
"""
from __future__ import annotations

import logging
from statistics import median

logger = logging.getLogger(__name__)

CORE_SEARCH_VERSION = "t6b"

# The window the ranking sorts on, and the window measured beside it. Named
# constants because the payload reports them and a test asserts the payload
# agrees with the search -- a window that is only a number in a fetch call is a
# window nobody can check.
RANK_WINDOW = "recent"
REFERENCE_WINDOW = "ten_year"

# How far the two windows' drawdowns may disagree before the candidate is
# flagged. 5 points is not a statistical threshold and is not presented as one:
# it is the size of gap that changes what a reader would do, and BND's real gap
# (19.9 points) clears it by a wide margin.
DISAGREE_DRAWDOWN_PCT = 5.0

# Below this, the gap between the best and the MEDIAN candidate is not a
# finding. Same constant and the same reasoning as `split_solver.NOISE_FLOOR_PCT`
# -- imported rather than restated would couple two searches that may want to
# diverge, so it is duplicated deliberately and the two are asserted equal in
# the tests, which is the coupling that actually matters.
NOISE_FLOOR_PCT = 0.25

# How many rows the card shows. The plan: "Report the top N by constrained
# excess, each with its drawdown. Ranking on return alone surfaces exactly the
# blends that are unholdable."
TOP_N = 5

# Named exclusion reasons. Every candidate that does not appear in the ranking
# appears in `excluded` with one of these; a candidate that silently vanishes is
# the PR 4 failure mode (eleven cards went missing and nothing said so).
NO_WEIGHTS = "NO_WEIGHTS"
NO_HISTORY = "NO_HISTORY"
NOT_MEASURABLE = "NOT_MEASURABLE"
CONCENTRATION = "CONCENTRATION"

# The baseline's id. It is the book's CURRENT core, and it is in the search so
# that "better" has something to be better than.
BASELINE_ID = "__current__"


def _f(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _window_of(metrics: dict, kind: str) -> dict:
    """What was actually measured, carried per candidate rather than per search.

    Per candidate because the candidates do NOT share a window: JEPI listed
    2020-05, so `inc_high_yield` has about six years where the other thirteen
    have ten. Reporting one window for the whole search would put a figure on
    the screen that is wrong for one row, and it would be wrong in the
    flattering direction -- a candidate that has not lived through 2022 shows no
    2022 drawdown.
    """
    return {"kind": kind,
            "start": metrics.get("start"),
            "end": metrics.get("end"),
            "sessions": metrics.get("observations")}


def _row(candidate: dict, rank_m: dict, ref_m: dict | None,
         *, max_drawdown_pct: float, conc: dict | None = None) -> dict:
    """One ranked row: the sorting window, the reference window, and the gap."""
    conc = conc or {"peak_ticker": None, "peak_pct": None, "breaches": False}
    rank_dd = _f(rank_m.get("max_drawdown_pct"))
    ref_dd = _f((ref_m or {}).get("max_drawdown_pct"))

    # Drawdowns arrive negative from `max_drawdown`. Compared as magnitudes so
    # "worse" reads as "bigger" here and in the ceiling test, and the ceiling
    # itself is a positive percentage the user typed.
    rank_depth = abs(rank_dd) if rank_dd is not None else None
    ref_depth = abs(ref_dd) if ref_dd is not None else None

    breaches_rank = rank_depth is not None and rank_depth > max_drawdown_pct
    breaches_ref = ref_depth is not None and ref_depth > max_drawdown_pct

    gap = None
    if rank_depth is not None and ref_depth is not None:
        gap = round(ref_depth - rank_depth, 2)

    flags: list[str] = []
    # The BND case, stated as a flag rather than left to arithmetic on the card.
    if gap is not None and gap >= DISAGREE_DRAWDOWN_PCT:
        flags.append("REFERENCE_DEEPER")
    if breaches_ref and not breaches_rank:
        flags.append("CEILING_BREACHED_ON_REFERENCE_ONLY")
    if breaches_rank and not breaches_ref:
        flags.append("CEILING_BREACHED_ON_RANK_ONLY")
    if ref_m is None:
        flags.append("REFERENCE_NOT_MEASURED")
    if conc.get("breaches"):
        flags.append("CONCENTRATION")

    return {
        "id": candidate["id"],
        "name": candidate.get("name") or candidate["id"],
        "goal": candidate.get("goal"),
        "source": candidate.get("source") or "catalog",
        "weights": dict(candidate.get("weights") or {}),
        # The sorting window.
        "excess_pct": _f(rank_m.get("excess_cagr_pct")),
        "cagr_pct": _f(rank_m.get("cagr_pct")),
        "max_drawdown_pct": rank_dd,
        "window": _window_of(rank_m, RANK_WINDOW),
        "peak_ticker": rank_m.get("peak_ticker"),
        "peak_ticker_weight_pct": _f(rank_m.get("peak_ticker_weight_pct")),
        # The window that contains 2022, measured for every row and not only
        # for the winner.
        "reference": None if ref_m is None else {
            "excess_pct": _f(ref_m.get("excess_cagr_pct")),
            "cagr_pct": _f(ref_m.get("cagr_pct")),
            "max_drawdown_pct": ref_dd,
            "window": _window_of(ref_m, REFERENCE_WINDOW),
        },
        "drawdown_gap_pct": gap,
        "breaches_ceiling": breaches_rank,
        "reference_breaches_ceiling": breaches_ref,
        # What the candidate would actually put in one ticker, book-wide.
        "concentration": {"peak_ticker": conc.get("peak_ticker"),
                          "peak_pct": conc.get("peak_pct"),
                          "breaches_cap": bool(conc.get("breaches"))},
        # Admissible means "this one is holdable under YOUR constraints", which
        # is both of them. Inadmissible rows are ranked below the admissible
        # ones and still SHOWN -- see the sort in `search()`.
        "admissible": not breaches_rank and not conc.get("breaches"),
        "flags": flags,
    }


def _concentration(metrics: dict, weights: dict, cap: float) -> dict:
    """The candidate's worst BOOK-LEVEL single-ticker weight, against the cap.

    THE FIRST VERSION OF THIS JUDGED THE BASKET'S OWN WEIGHTS AND WAS WRONG. It
    excluded eight of the fourteen families on a 40% cap -- including
    `bal_6040` (VTI 60% of the basket) and `pre_capital` (BIL 60%) -- because it
    compared a share OF THE CORE against a cap on a share OF THE BOOK. A core
    that is 36% of NAV holding VTI at 60% of itself puts 21.6% of the book in
    VTI, which is nowhere near a 40% cap. Refusing "60/40" for concentration is
    the kind of answer that is obviously wrong to a reader and quietly wrong to
    a test.

    So the peak is READ FROM THE MEASUREMENT rather than recomputed: the caller
    measures the candidate at its real share alongside the sleeves, and
    `measure_blend` already returns `peak_weight_pct_by_ticker` built from the
    same blended targets the metrics came from. Its own comment says why that
    is the authoritative copy -- "a solver recomputing it alongside would be a
    second implementation of one fact", which is the defect PR 9 existed to
    remove. Restricted to the core's OWN tickers, because a sleeve breaching the
    cap is not this candidate's doing.
    """
    peaks = metrics.get("peak_weight_pct_by_ticker") or {}
    mine = {tk: pct for tk, pct in peaks.items() if tk.upper() in
            {t.upper() for t in (weights or {})}}
    if not mine:
        return {"peak_ticker": None, "peak_pct": None, "breaches": False}
    tk = max(mine, key=mine.get)
    pct = float(mine[tk])
    cap_pct = cap * 100.0
    return {"peak_ticker": tk, "peak_pct": round(pct, 2),
            "breaches": cap_pct > 0 and pct > cap_pct}


def search(candidates: list[dict], measure_rank, measure_reference, *,
           max_drawdown_pct: float, concentration_cap: float = 1.0,
           top_n: int = TOP_N, baseline: dict | None = None,
           backstop_hit: bool = False) -> dict:
    """Rank candidate cores on the recent window, measured against the long one.

    `measure_rank(candidate)` and `measure_reference(candidate)` each return a
    `measure_blend`-shaped dict; a falsy `ok` is an abstention and the candidate
    is EXCLUDED WITH A REASON rather than dropped. `concentration_cap` is a
    FRACTION (0..1), matching `plan_service.effective_caps`.

    `baseline` is the book's current core. It is measured and ranked like any
    other candidate and exempt from the cap only.
    """
    pool: list[dict] = []
    if baseline and (baseline.get("weights")):
        pool.append({**baseline, "id": baseline.get("id") or BASELINE_ID,
                     "name": baseline.get("name") or "Your current holdings",
                     "source": "baseline"})
    pool.extend(candidates or [])

    rows: list[dict] = []
    excluded: list[dict] = []

    for cand in pool:
        weights = cand.get("weights") or {}
        cid = cand.get("id")
        name = cand.get("name") or cid
        if not weights:
            excluded.append({"id": cid, "name": name, "reason": NO_WEIGHTS,
                             "detail": "this family carries no basket"})
            continue

        rank_m = measure_rank(cand) or {}
        if not rank_m.get("ok"):
            excluded.append({
                "id": cid, "name": name,
                "reason": NO_HISTORY if rank_m.get("reason") in
                ("NO_OVERLAP", "INSUFFICIENT_HISTORY") else NOT_MEASURABLE,
                "detail": f"{rank_m.get('reason') or 'no result'} "
                          f"{rank_m.get('detail') or ''}".strip()})
            continue

        # The reference is allowed to fail on its own. A candidate measurable
        # over one year and not over ten is still rankable -- it just cannot say
        # what it did in 2022, and REFERENCE_NOT_MEASURED says exactly that
        # rather than letting a blank read as "nothing happened".
        ref_m = measure_reference(cand) or {}

        # Judged AFTER the measurement, from the measurement, and only for
        # candidates the search is proposing. The baseline is the core the
        # reader already owns: its concentration is a fact about their book,
        # not a property of this decision, and letting it mark the baseline
        # inadmissible would rank every alternative above it for a reason that
        # has nothing to do with the alternatives. `target_solver.solve` draws
        # the same line, in its own words: judging a fixed core against the cap
        # "lets an already-concentrated core veto every sleeve size including
        # zero, which says nothing about the choice being made".
        conc = ({"peak_ticker": None, "peak_pct": None, "breaches": False}
                if cand.get("source") == "baseline"
                else _concentration(rank_m, weights, concentration_cap))

        rows.append(_row(cand, rank_m, ref_m if ref_m.get("ok") else None,
                         max_drawdown_pct=max_drawdown_pct, conc=conc))

    # Constrained excess: inadmissible rows sort below every admissible one
    # rather than being deleted. The plan asks for "the top N by constrained
    # excess, each with its drawdown" -- a candidate that wins on return and
    # breaches the ceiling is the single most useful row on the screen, and
    # dropping it would leave the reader wondering why the obvious name is
    # missing.
    rows.sort(key=lambda r: (r["admissible"], r["excess_pct"] if r["excess_pct"]
                             is not None else float("-inf")), reverse=True)

    admissible = [r for r in rows if r["admissible"] and r["excess_pct"] is not None]
    spread = None
    if admissible:
        vals = [r["excess_pct"] for r in admissible]
        best, mid, worst = max(vals), median(vals), min(vals)
        spread = {
            "best_excess_pct": round(best, 2),
            "median_excess_pct": round(float(mid), 2),
            "worst_excess_pct": round(worst, 2),
            "best_minus_median_pct": round(best - float(mid), 2),
            # The honest headline. If the winner beats the median by less than
            # the noise floor, the choice barely matters and the ordinal is
            # doing work the measurement does not support.
            "above_noise_floor": (best - float(mid)) >= NOISE_FLOOR_PCT,
        }

    # Handed to the card so it cannot render the ranking without the
    # contradiction. Derived here rather than in a renderer for the reason
    # PR 9 existed: two implementations of one fact eventually disagree.
    disagreement = [
        {"id": r["id"], "name": r["name"], "flags": r["flags"],
         "rank_max_drawdown_pct": r["max_drawdown_pct"],
         "reference_max_drawdown_pct": (r["reference"] or {}).get("max_drawdown_pct"),
         "drawdown_gap_pct": r["drawdown_gap_pct"]}
        for r in rows if r["flags"]]

    if backstop_hit:
        # "A limit that binds silently reads as a finding." -- the plan.
        logger.warning("core_search: runtime backstop stopped the search at "
                       "%d measured candidates", len(rows))

    baseline_row = next((r for r in rows if r["source"] == "baseline"), None)

    # THE ANSWER MAY BE "NONE OF THEM", AND THAT IS A RESULT, NOT AN EMPTY LIST.
    #
    # On the first live run against the real book every one of the fifteen
    # candidates was inadmissible: the shallowest drawdown on offer was 31.92%
    # against a 13.3% ceiling. With 64% of the book in TQQQ and SOXL the sleeves
    # dominate, and no choice of core repairs a ceiling they have already
    # broken.
    #
    # That is the single most useful thing this search can say, and without this
    # field a renderer would show a ranked table of fifteen rows and let the
    # reader infer that the top one is a fix. It is not. The existing card tells
    # him to change the core; this measurement says changing the core is not
    # enough, and the payload has to be able to contradict the advice.
    shallowest = None
    for r in rows:
        d = r["max_drawdown_pct"]
        if d is not None and (shallowest is None or abs(d) < abs(shallowest)):
            shallowest = d
    if rows and not admissible:
        verdict = {
            "kind": "NONE_ADMISSIBLE",
            "headline": (
                f"None of the {len(rows)} cores measured comes in under your "
                f"{max_drawdown_pct:.1f}% ceiling. The shallowest is "
                f"{abs(shallowest):.1f}%." if shallowest is not None else
                f"None of the {len(rows)} cores measured comes in under your "
                f"{max_drawdown_pct:.1f}% ceiling."),
            "means": ("The core is not what is breaching the ceiling. Changing it "
                      "cannot repair a drawdown your sleeves already produce, so "
                      "the choice in front of you is the sleeves or the ceiling, "
                      "not the core."),
            "shallowest_pct": shallowest,
        }
    elif rows:
        verdict = {"kind": "RANKED", "headline": None, "means": None,
                   "shallowest_pct": shallowest}
    else:
        verdict = {"kind": "NOTHING_MEASURED",
                   "headline": "No candidate could be measured.",
                   "means": "Every candidate is listed in `excluded` with its reason.",
                   "shallowest_pct": None}

    return {
        "verdict": verdict,
        "ok": True,
        "version": CORE_SEARCH_VERSION,
        # Never implicit. "Best over one year" and "best" are different claims.
        "ranked_on": {
            "window": RANK_WINDOW,
            "metric": "excess_cagr_pct",
            "constraint": f"max drawdown <= {max_drawdown_pct:.1f}%",
            "caveat": ("ranked on the recent window, which prices these sleeves' "
                       "own drawdown but understates the 2022 rate shock for "
                       "bond-heavy baskets -- the ten-year figure is on every row"),
        },
        "reference_window": REFERENCE_WINDOW,
        "max_drawdown_pct": max_drawdown_pct,
        "concentration_cap_pct": round(concentration_cap * 100, 2),
        "ranked": rows[:top_n],
        "ranked_count": len(rows),
        "baseline": baseline_row,
        "excluded": excluded,
        "spread": spread,
        "ceiling_disagreement": disagreement,
        "coverage": {
            "candidates_offered": len(pool),
            "measured": len(rows),
            "excluded": len(excluded),
            "shown": min(top_n, len(rows)),
            "runtime_backstop_hit": bool(backstop_hit),
        },
    }
