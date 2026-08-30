"""The objective reaches the screener, and no invented tilt rides in with it.

Eran's item 2: *"make sure all of them are aligned with the strategy/strategies
that I pick to manage my portfolio."* Then, before deploying the first cut:
*"I don't want anything to be hardcoded -- everything must be calculated."*

Both constraints at once, and they pull in opposite directions. Aligning the
screener with the objective needs a factor tilt; nothing in this codebase
derives one. `DEFAULT_WEIGHTS` has no stated provenance, and the strategy
catalogue carries `target_allocation` and `basket` and no factor information.
The one derivation available -- the factor signature of each objective's own
baskets -- was computed and produced Income weighting GROWTH highest and INCOME
lowest, because the sandbox provider seeds fundamentals off a sha1 of the
ticker. It cannot be validated here.

So this PR ships the WIRING and NO WEIGHTS.

WHAT THESE TESTS PIN
================================================================================

  * the objective travels from the plan to the screener, at every call site
  * the coverage of each ranking is reported rather than silently truncated
  * the table is EMPTY, and stays empty until someone adds a row deliberately
  * an empty table changes NOTHING about how any book ranks

The last one is the safety property. A wiring change that quietly reordered
real buy recommendations would be worse than the bug it fixes.
"""
import pytest

from app.engines.screener_engine import DEFAULT_WEIGHTS
from app.services import objective_weights as ow

# AN EXPLICIT CANDIDATE LIST, AND THE REASON IS A BUG THIS PR ALSO SURFACES.
#
# The first cut of these tests called `screen_equities()` with no candidates and
# compared separate calls. It passed alone and FAILED intermittently in the
# suite. The cause was not the test: the provider tier's bucket is
# `TokenBucket(20, 20)` and the universe is 58 names, so a scan silently ranks
# whichever ~20 the bucket allows, and how many tokens remain depends on what
# ran before it.
#
# Twelve names stays inside one refill window, so any comparison is between two
# rankings of the SAME set -- the only comparison that means anything.
_CANDIDATES = [
    {"ticker": t, "name": t, "market": "NASDAQ", "kind": "stock", "asset_class": "Equities"}
    for t in ("AAPL", "MSFT", "JNJ", "PFE", "KO", "PG",
              "NVDA", "AMD", "V", "MA", "XOM", "CVX")
]


# --------------------------------------------------------------------------
# No invented numbers
# --------------------------------------------------------------------------

def test_no_tilt_is_defined_for_any_objective():
    """THE ONE THAT ENFORCES 'NOTHING HARDCODED'.

    Adding a row here changes which securities get recommended. This test makes
    that a deliberate act with a conversation attached, rather than four numbers
    appearing in a diff.

    If you are here because you added a legitimate, CALCULATED tilt: update this
    test to assert how it is derived, not merely that it exists.
    """
    assert ow.OBJECTIVE_WEIGHTS == {}, (
        f"a factor tilt has appeared: {ow.OBJECTIVE_WEIGHTS}. Nothing in this "
        "codebase derives one -- DEFAULT_WEIGHTS has no provenance and the "
        "strategy catalogue carries no factor data. If these are calculated, "
        "say from what. If they are a human judgement, say whose.")


@pytest.mark.parametrize("objective", ow.OBJECTIVES)
def test_no_objective_is_tilted_and_none_claims_to_be(objective):
    """A card must not say it was tailored when it was not."""
    assert ow.weights_for(objective) is None
    assert ow.is_tilted(objective) is False
    assert ow.tilt_disclosure(objective) is None, (
        f"{objective} disclosed a tilt that does not exist")


def test_the_version_says_there_is_no_tilt():
    """A pin on an old recommendation has to be readable years later."""
    assert "no-tilt" in ow.WEIGHTS_VERSION, (
        "WEIGHTS_VERSION no longer records that no tilt is defined; if a tilt "
        "was added, bump it to a real version")


def test_an_unknown_objective_falls_back_rather_than_guessing():
    """The quiet kind of wrong this avoids.

    A new objective added to the plan and not added to the table must rank on
    DEFAULT_WEIGHTS -- the pre-existing behaviour -- never on whichever row
    happened to look closest.
    """
    for value in ("Aggressive", "", None):
        assert ow.weights_for(value) is None
        assert ow.is_tilted(value) is False


def test_the_structural_guards_are_real_and_not_decorative():
    """They pass trivially on an empty table, so prove they would bite.

    A row that does not sum to 1.0 makes composites incomparable across
    objectives; a row missing a factor makes that factor contribute zero without
    saying so. Both must be rejected at import.
    """
    original = dict(ow.OBJECTIVE_WEIGHTS)
    try:
        ow.OBJECTIVE_WEIGHTS["Grow"] = {"value": .5, "growth": .9,
                                        "quality": .1, "income": .1}
        with pytest.raises(ValueError, match="sum to"):
            ow._assert_sane()

        ow.OBJECTIVE_WEIGHTS["Grow"] = {"value": .5, "growth": .5}
        with pytest.raises(ValueError, match="screener scores"):
            ow._assert_sane()

        ow.OBJECTIVE_WEIGHTS.clear()
        ow.OBJECTIVE_WEIGHTS["Turbo"] = dict(DEFAULT_WEIGHTS)
        with pytest.raises(ValueError, match="not an objective"):
            ow._assert_sane()
    finally:
        ow.OBJECTIVE_WEIGHTS.clear()
        ow.OBJECTIVE_WEIGHTS.update(original)
    ow._assert_sane()   # the real table must still be valid afterwards


# --------------------------------------------------------------------------
# The safety property: wiring the objective through changed nothing
# --------------------------------------------------------------------------

def _rank(objective):
    from app.agents.screener_agent import OpportunityAgent
    picks = OpportunityAgent().screen_equities(
        weights=ow.weights_for(objective), top_n=8, candidates=_CANDIDATES)
    return [p.ticker for p in picks]


def test_every_objective_still_ranks_exactly_as_it_did_before():
    """THE SAFETY PROPERTY OF THIS PR.

    The wiring is in place, and because the table is empty it must change
    nothing for anybody. A change that quietly reordered real buy
    recommendations while claiming to be plumbing would be worse than the bug.
    """
    from app.agents.screener_agent import OpportunityAgent
    untilted = [p.ticker for p in OpportunityAgent().screen_equities(
        top_n=8, candidates=_CANDIDATES)]
    assert untilted, "the screener returned nothing; the rest proves nothing"

    for objective in ow.OBJECTIVES:
        assert _rank(objective) == untilted, (
            f"{objective} now ranks differently from the untilted default. If a "
            "tilt was added deliberately, this test needs rewriting -- but "
            "check first that it was deliberate.")


def test_the_mechanism_would_work_the_moment_a_tilt_exists():
    """The wiring is provably live, not merely present.

    Uses a THROWAWAY vector that is never shipped and is not a proposal -- it
    exists only to show the weights argument reaches ScreenerEngine and changes
    the order. Without this, an empty table and a broken pipe look identical.
    """
    from app.agents.screener_agent import OpportunityAgent

    ag = OpportunityAgent()
    flat = [p.ticker for p in ag.screen_equities(top_n=8, candidates=_CANDIDATES)]
    extreme = [p.ticker for p in ag.screen_equities(
        weights={"value": 0.0, "growth": 0.0, "quality": 0.0, "income": 1.0},
        top_n=8, candidates=_CANDIDATES)]
    assert flat != extreme, (
        "ranking on income alone produced the identical order as the default -- "
        "the weights argument is not reaching ScreenerEngine, so an empty table "
        "would be hiding a broken pipe rather than an absent decision")


# --------------------------------------------------------------------------
# The pin, and the coverage it carries
# --------------------------------------------------------------------------

def test_the_audit_pin_records_that_no_tilt_was_applied():
    """Silence would be indistinguishable from 'nobody recorded it'."""
    pin = ow.audit_block("Income")
    assert pin["objective"] == "Income"
    assert pin["weights"] is None
    assert pin["tilted"] is False
    assert pin["weights_version"] == ow.WEIGHTS_VERSION
    assert "DEFAULT_WEIGHTS" in pin["source"], (
        "the pin must say the ranking fell back to the default, not go quiet")


def test_the_ranking_says_how_much_of_the_universe_it_covered():
    """A ranking of 20 names out of 58 is not the claim a ranking of 58 makes.

    `_fundamentals_for` used to swallow every exception into `f = None` and drop
    the candidate with no counter and no log, so a name that was never scored was
    indistinguishable from one that scored badly. MEASURED in the sandbox: 58
    candidates in, 20 ranked, 38 RateLimitedError -- the survivors being
    literally the first twenty entries of the universe, and all seven Israeli
    names among the dropped.

    This does not assert HOW MANY resolve; that depends on the provider and the
    machine. It asserts the count is REPORTED, so the question is answerable.
    """
    from app.agents.screener_agent import OpportunityAgent

    ag = OpportunityAgent()
    ag.screen_equities(top_n=5, candidates=_CANDIDATES)
    cov = ag.last_coverage

    assert cov, "the screener ranked without recording what it could reach"
    assert cov["requested"] == len(_CANDIDATES)
    assert cov["ranked"] + len(cov["unreachable"]) + len(cov["no_data"]) == cov["requested"], (
        f"candidates went missing without being counted: {cov}")
    assert isinstance(cov["unreachable"], list) and isinstance(cov["no_data"], list), (
        "unreachable and no_data must name the tickers, not just count them -- "
        "'38 dropped' does not tell you Israeli names were among them")


def test_buy_ideas_passes_the_objective_through_and_pins_it():
    """The call site itself, not just the helper it calls.

    `_buy_ideas` feeds the Today cards and the redeploy-cash leg that can create
    a NEW POSITION. Accepting the argument and ignoring it would pass every
    other test in this file.
    """
    from app.services.recommendations import _buy_ideas

    snap = {"avg_volatility_pct": 15.0}
    ideas = _buy_ideas(snap, "ILS", "Income")
    assert ideas, "the screener produced no ideas; nothing to check"

    pins = [i["ranked_for"] for i in ideas if i.get("ranked_for")]
    assert pins, "no buy idea carries the tilt that ranked it"
    assert any(p["objective"] == "Income" for p in pins), (
        "_buy_ideas accepted an objective and did not record it -- the argument "
        "may be being ignored")
    assert all(p["tilted"] is False for p in pins), (
        "a buy idea claims it was tilted while the table is empty")
    assert any("coverage" in p for p in pins), (
        "the pin does not carry how much of the universe was ranked")

    # And with the table empty, the objective must not change the outcome.
    grow = [i["ticker"] for i in _buy_ideas(snap, "ILS", "Grow")]
    income = [i["ticker"] for i in _buy_ideas(snap, "ILS", "Income")]
    assert grow == income, (
        "the objective changed the buy ideas while no tilt is defined")
