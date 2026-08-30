"""The screener's factor tilt, per objective -- WIRED, AND DELIBERATELY EMPTY.

Eran, 2026-08-30, item 2: *"make sure all of them are aligned with the
strategy/strategies that I pick to manage my portfolio."* And then, before
deploying the first cut of this: *"I don't want anything to be hardcoded --
everything must be calculated and not hardcoded."*

He was right, and the first cut of this file failed that test.

WHAT WAS ACTUALLY BROKEN
================================================================================

`recommendations.py` called `screen_equities(top_n=5)` with no weights, so
`ScreenerEngine` used `DEFAULT_WEIGHTS` for every book. Grow and Preserve ranked
the identical candidates in the identical order. The objective influenced HOW
MUCH was bought -- through the target mix and the funding logic -- and never
WHAT. The one code path that could supply weights was a query string on
`/api/v1/screener`, and nothing in the app ever built that URL.

The plumbing existed and reached nobody: `plan_service.py:55` writes `objective`
into the settings object every engine holds, and grepping the tree for a read of
it returns nothing.

THIS FILE FIXES THE WIRING AND SHIPS NO WEIGHTS
================================================================================

`OBJECTIVE_WEIGHTS` is EMPTY. `weights_for()` therefore returns None for every
objective, `ScreenerEngine` uses `DEFAULT_WEIGHTS`, and **the ranking is
identical to before this file existed, for every book**.

That is the point. The wiring is now in place and provably reaches the screener
(`tests/test_the_objective_reaches_the_screener.py`), so the day there is a
defensible basis for a tilt it is four numbers away. Until then nothing is
invented.

WHY EMPTY, WITH THE EVIDENCE
================================================================================

The first cut carried four hand-chosen rows behind an `OWNER_REVIEWED = False`
flag. That flag is an admission of a problem, not a solution to it: the numbers
still shipped, still reordered real buy recommendations, and still had no
provenance. `DEFAULT_WEIGHTS` itself is four numbers with no stated derivation,
so "close to the default" is not a defence either.

THE DERIVATION WAS ATTEMPTED, AND IT DID NOT SURVIVE MEASUREMENT.

The obvious candidate: each objective's own strategies in `strategies.py` carry
a real `basket` of tickers, so the factor signature of those baskets could BE
the tilt -- calculated from live fundamentals, from data the app already holds
and the user can already see. Grow's baskets resolve to 14 names, Balanced 5,
Income 6, Preserve 6, and every one of them resolves fundamentals.

Computed end to end, percentiles taken against the whole candidate universe
plus the basket names (69 tickers), the sandbox produced:

    Grow      value .241  growth .292  quality .253  income .214
    Balanced  value .213  growth .224  quality .278  income .285
    Income    value .229  growth .322  quality .252  income .196
    Preserve  value .262  growth .238  quality .212  income .288

**Income came out weighting growth HIGHEST and income LOWEST** -- the exact
contradiction the tilt was supposed to remove, reproduced by the derivation. All
four vectors sit inside 0.20-0.33 and would barely differentiate anything.

The cause is not subtle: `BuiltinMarketDataProvider.get_fundamentals` seeds
every field off a sha1 of the ticker (`builtin.py:56-81`). SCHD's dividend
yield in this sandbox is a hash, not a yield. So the basket signature here is
noise, and the method cannot be validated on the only data this environment has.

It might work on production's Yahoo fundamentals. It might also not: the Income
and Preserve baskets are almost entirely ETFs (SCHD, VYM, VIG, JEPI, HYG, BND,
BIL, SHY, TIP, IAU), and pass-through aggregates of P/E, P/B and ROE are weak
inputs for exactly the objectives where the tilt matters most.

So there are two honest options and this file takes the second:

  1. ship numbers that look derived and are not validated -- which is the
     `strategy_profile.assumptions_for()` failure PR 16 exists to undo, where a
     static table priced TQQQ at 12%/yr and 55% vol BUY-AND-HOLD and four
     screens inherited it looking measured; or
  2. ship the wiring, ship the counter that makes the question answerable, and
     leave the table empty until there is a basis that survives measurement.

Item 2 stays OPEN, with a proven reason rather than an oversight.
"""
from __future__ import annotations

from app.engines.screener_engine import DEFAULT_WEIGHTS

# Bumped whenever a row is added or changed. A recommendation pinned to an older
# version was produced by a different tilt and must not be silently reused.
# 0.0 means: no tilt has ever been defined.
WEIGHTS_VERSION = "0.0-no-tilt"

# Meaningless while the table is empty, and kept so that the day a row is added
# it CANNOT be added without a person deciding this too.
OWNER_REVIEWED = False

# The objectives `Plan.objective` can hold (models/tables.py:309).
OBJECTIVES = ("Grow", "Balanced", "Income", "Preserve")

# EMPTY, DELIBERATELY. See the module docstring: the hand-chosen rows had no
# provenance, and the one derivation available did not survive measurement.
#
# Adding a row here CHANGES WHICH SECURITIES ARE RECOMMENDED. Whatever is put in
# it must be calculated from data the app holds, or stated plainly as a human
# judgement with OWNER_REVIEWED reflecting who made it -- not both, and never
# neither.
OBJECTIVE_WEIGHTS: dict[str, dict[str, float]] = {}


def _assert_sane() -> None:
    """Structural guarantees for whatever the table eventually holds.

    None of these is an investment opinion -- they are the properties any
    opinion expressed here must have to be coherent. They run at import, so a
    malformed row cannot reach a recommendation.

    An EMPTY table passes every one of them, which is correct: shipping no tilt
    is a valid state, and the only one currently justified.
    """
    factors = set(DEFAULT_WEIGHTS)
    for name, row in OBJECTIVE_WEIGHTS.items():
        if name not in OBJECTIVES:
            raise ValueError(
                f"weights defined for {name!r}, which is not an objective the "
                f"plan can hold ({', '.join(OBJECTIVES)}) -- it would never apply")
        if set(row) != factors:
            raise ValueError(
                f"objective weights for {name!r} cover {sorted(row)}, but the "
                f"screener scores {sorted(factors)} -- a factor scored and not "
                "weighted silently contributes zero")
        total = round(sum(row.values()), 6)
        if total != 1.0:
            raise ValueError(
                f"objective weights for {name!r} sum to {total}, not 1.0; "
                "composite scores across objectives would not be comparable")


_assert_sane()


def weights_for(objective: str | None) -> dict[str, float] | None:
    """The factor tilt for an objective, or None to leave the default alone.

    RETURNS None RATHER THAN A GUESS, for an unknown objective and -- while the
    table is empty -- for every objective. None means `ScreenerEngine` uses
    DEFAULT_WEIGHTS, which is exactly the behaviour that existed before this
    module: no better, and no invented tilt either.

    The alternative, ranking an objective under whichever row happened to look
    closest, is the quiet kind of wrong.
    """
    if not objective:
        return None
    return OBJECTIVE_WEIGHTS.get(str(objective).strip())


def is_tilted(objective: str | None) -> bool:
    """Does this objective rank differently from the pre-existing default?"""
    w = weights_for(objective)
    return w is not None and w != DEFAULT_WEIGHTS


def tilt_disclosure(objective: str | None) -> str | None:
    """The sentence a card must carry when a tilt reordered it.

    None while the table is empty -- there is nothing to disclose, and claiming
    a ranking was tailored when it was not would be its own dishonesty.
    """
    if not is_tilted(objective):
        return None
    if OWNER_REVIEWED:
        return (f"Ranked for your {objective} objective "
                f"(factor tilt v{WEIGHTS_VERSION}).")
    return (f"Ranked for your {objective} objective using a factor tilt that "
            f"has NOT been reviewed yet (v{WEIGHTS_VERSION}) -- the direction "
            f"follows the objective, the exact weights are a proposal.")


def audit_block(objective: str | None) -> dict:
    """Reproducibility pin: what tilt produced this ranking, and was it reviewed.

    Carried on the card so a recommendation read next month traces to the
    weights that made it rather than to whatever the table says by then. While
    the table is empty this records, truthfully, that no tilt was applied --
    which is the fact a reader of an old recommendation will need.
    """
    w = weights_for(objective)
    return {
        "objective": objective or None,
        "weights_version": WEIGHTS_VERSION,
        "owner_reviewed": OWNER_REVIEWED,
        "weights": dict(w) if w is not None else None,
        "tilted": w is not None,
        "source": ("objective_weights.OBJECTIVE_WEIGHTS" if w is not None
                   else "screener_engine.DEFAULT_WEIGHTS (no tilt defined for "
                        "this objective)"),
    }
