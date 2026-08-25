"""T6b - discover how many sleeves are worth running, and which.

T6a sizes the sleeves you already hold. This asks the wider question the plan
sets: *which* strategies, and *how many*. Two rules from the plan drive the
whole design, and both are about refusing to overclaim.

**"Discover the sleeve count, don't cap it."** Grow the set while each addition
improves the constrained objective; stop when it stops; and REPORT where it
stopped and by how much the last one helped. The count is then a measurement
rather than a number someone picked. A hard runtime ceiling stays as a backstop
-- and if the backstop is what stopped the search, that is said out loud,
because *a limit that binds silently reads as a finding*.

**"No auto-apply, ever, from a ranked list."** A single solve has one answer and
can carry an Accept button. A ranked list of blends cannot: picking row 1 of 40
is selection on one sample wearing the authority of a recommendation. Every
result here is marked `auto_apply_forbidden`, and the reason travels with it.

**The line this must not cross.** `investing-discipline` section 5: the app never
recommends products. Candidates come only from ids the caller supplies out of
`strategy_catalog` / `strategies.CATALOG` -- the user's own configured rules. A
ranked list of *your own rules with their measured numbers* is a measurement. The
moment this module picked a ticker it would be recommending a security, so it
never sees one: `add_candidate` takes ids, and `forbidden_tickers` exists to make
a violation catchable rather than merely unlikely.

Greedy forward selection, not exhaustive. With 7 catalog strategies an exhaustive
search over subsets AND sizes is millions of simulations; greedy is a few dozen.
It can miss a pair that only works together -- so `coverage.method` says so in
the payload rather than in this docstring, where the user cannot read it.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

CANDIDATE_VERSION = "t6b"

# An addition has to beat the incumbent by MORE than this to be kept. Without a
# floor, greedy selection adds sleeves forever: every extra degree of freedom
# improves an in-sample fit by SOMETHING, and "it improved by 0.03%/yr" is a
# measurement of noise being sold as a discovery.
MIN_GAIN_PCT = 0.25

# Backstops. Neither is a finding; both are reported when they bind.
MAX_SLEEVES = 6
MAX_EVALUATIONS = 240


def grow(measure, *, candidates: list[str], start: list[str] | None = None,
         min_gain_pct: float = MIN_GAIN_PCT,
         max_sleeves: int = MAX_SLEEVES,
         max_evaluations: int = MAX_EVALUATIONS) -> dict:
    """Add sleeves while each addition improves the objective. Then stop, and say why.

    `measure(ids) -> {"score_pct": float, "admissible": bool, ...} | None`
    where `ids` is the sleeve set being evaluated. `score_pct` is the caller's
    objective -- T6a ranks out-of-sample, and this inherits that choice rather
    than making a second one.

    Returns the chosen set, the marginal gain of every accepted addition, and a
    `stopped` block that distinguishes "nothing more helped" from "a limit bound".
    """
    chosen = list(start or [])
    pool = [c for c in candidates if c not in chosen]
    evaluated = 0
    scores: dict[tuple, dict] = {}

    def score(ids):
        nonlocal evaluated
        key = tuple(sorted(ids))
        if key in scores:
            return scores[key]
        if evaluated >= max_evaluations:
            return None
        evaluated += 1
        m = measure(list(key))
        scores[key] = m
        return m

    base = score(chosen) if chosen else None
    best_score = (base or {}).get("score_pct") if base and base.get("admissible") else None

    steps = []
    stopped = None
    while True:
        if len(chosen) >= max_sleeves:
            stopped = {"reason": "sleeve_cap",
                       "detail": (f"stopped at the {max_sleeves}-sleeve backstop, not "
                                  f"because adding another stopped helping. This is a "
                                  f"LIMIT, not a finding -- raise it to see further."),
                       "is_a_limit": True}
            break
        if evaluated >= max_evaluations:
            stopped = {"reason": "evaluation_budget",
                       "detail": (f"stopped after {evaluated} evaluations, the runtime "
                                  f"backstop. A limit, not a finding."),
                       "is_a_limit": True}
            break
        if not pool:
            stopped = {"reason": "candidates_exhausted",
                       "detail": "every candidate offered has been tried",
                       "is_a_limit": False}
            break

        best_add, best_m = None, None
        for c in pool:
            m = score(chosen + [c])
            if m is None:
                continue
            if not m.get("admissible") or m.get("score_pct") is None:
                continue
            if best_m is None or m["score_pct"] > best_m["score_pct"]:
                best_add, best_m = c, m

        if best_add is None:
            stopped = {"reason": "nothing_admissible",
                       "detail": ("no remaining candidate produced an admissible blend "
                                  "-- every one breached the ceiling or a cap"),
                       "is_a_limit": False}
            break

        gain = (best_m["score_pct"] - best_score) if best_score is not None else best_m["score_pct"]
        if best_score is not None and gain <= min_gain_pct:
            stopped = {"reason": "no_further_gain",
                       "detail": (f"the best remaining addition ({best_add}) was worth "
                                  f"{gain:+.2f}%/yr, inside the {min_gain_pct}%/yr noise "
                                  f"floor. THIS is the measured answer: {len(chosen)} "
                                  f"sleeve(s), because the next one did not help."),
                       "is_a_limit": False,
                       "rejected": {"id": best_add, "gain_pct": round(gain, 2)}}
            break

        chosen.append(best_add)
        pool.remove(best_add)
        steps.append({"added": best_add, "n_sleeves": len(chosen),
                      "score_pct": round(best_m["score_pct"], 2),
                      "marginal_gain_pct": round(gain, 2),
                      "max_drawdown_pct": best_m.get("max_drawdown_pct")})
        best_score = best_m["score_pct"]

    ranked = sorted(
        [{"ids": list(k), "n_sleeves": len(k),
          "score_pct": round(m["score_pct"], 2),
          "max_drawdown_pct": m.get("max_drawdown_pct")}
         for k, m in scores.items()
         if m and m.get("admissible") and m.get("score_pct") is not None],
        key=lambda r: -r["score_pct"])

    return {
        "ok": bool(chosen) or bool(ranked),
        "chosen": chosen,
        "n_sleeves": len(chosen),
        "steps": steps,
        # The plan's exact requirement: report where it stopped AND by how much
        # the last addition helped. Both, together, or the count reads as a rule.
        "last_addition_gain_pct": steps[-1]["marginal_gain_pct"] if steps else None,
        "stopped": stopped,
        "ranked": ranked,
        "coverage": {
            "evaluations": evaluated,
            "candidates_offered": len(candidates),
            "method": ("greedy forward selection -- each round adds the single best "
                       "remaining candidate. NOT exhaustive: a pair that only works "
                       "together can be missed, because neither half improves the set "
                       "alone."),
        },
        # Carried in the payload, not assumed by the renderer. A flag the UI has
        # to read is a flag that can be forgotten; a flag the API always sends is
        # one the UI has to actively ignore.
        "auto_apply_forbidden": True,
        "auto_apply_note": ("this is a RANKED LIST, not a solve. Picking the top row of "
                            "a list searched over one history is selection on one "
                            "sample. Size a chosen blend through the normal solve, and "
                            "accept it there."),
        "candidate_version": CANDIDATE_VERSION,
    }


def forbidden_tickers(result: dict, allowed_ids: set[str]) -> list[str]:
    """Any id in the result that was not offered as a candidate.

    `investing-discipline` section 5 -- the app never recommends products. The
    search only ever sees strategy IDS from the user's own catalog, so a ticker
    cannot enter by construction. This makes that catchable rather than merely
    true today: if a future change lets a security in, a test fails instead of a
    card quietly recommending one.
    """
    seen = set(result.get("chosen") or [])
    for r in result.get("ranked") or []:
        seen |= set(r.get("ids") or [])
    for s in result.get("steps") or []:
        if s.get("added"):
            seen.add(s["added"])
    return sorted(seen - set(allowed_ids))
