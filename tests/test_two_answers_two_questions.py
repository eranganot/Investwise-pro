"""PR 14b -- two Accept buttons, two different numbers, and nothing said why.

The Target card offers "Set your sleeves to 65% of the book" and, right below
it, "Best split found: 40% / 40%". Both are green Accept buttons. Both are
correct. They differ because they answer **different questions**:

    T2   `solve`        the SMALLEST sleeve share that REACHES a target you typed
    T6a  `solve_split`  the LARGEST excess your ceiling ALLOWS, with no target

T2's own card already says *"Anything larger buys return you did not ask for at
risk you did not agree to."* T6a's docstring already says *"No `excess_pct`
parameter, deliberately."* Neither said it where the reader could see both
numbers at once, which is the card-claim invariant broken across two cards
instead of inside one.

**And the split winner was clipped, not optimised.** Every one of the top eight
splits on the live book had 40 in the first column, because each sleeve holds a
single ticker and the concentration cap is 0.40 -- a 40% sleeve IS that ticker
at exactly the cap. The search ran into a wall and stopped. `coverage.method`
already said "the best point REACHED, not a proven global optimum", which is
true and still reads as "optimised" to anyone not looking for the distinction.

Measured on the real book: winner `[40.0, 40.0]`, 4 of 6 neighbours
inadmissible, binding constraint `concentration_cap` on **QQQ** -- the dip-buy
rule's BASE holding, not its leveraged leg.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.services import split_solver as sp  # noqa: E402


def _measure_factory(best, cap=None):
    """A landscape whose peak is at `best`, optionally clipped by a cap."""
    def measure(point):
        if cap is not None and any(w > cap + 1e-9 for w in point):
            return {"excess_pct": 99.0, "oos_excess_pct": 99.0,
                    "max_drawdown_pct": 10.0, "admissible": False}
        # Distance from `best`, negated, so `best` scores highest.
        d = sum(abs(w - b) for w, b in zip(point, best))
        return {"excess_pct": 10.0 - d, "oos_excess_pct": 10.0 - d,
                "max_drawdown_pct": 10.0, "admissible": True}
    return measure


# ===========================================================================
# clipped vs optimised
# ===========================================================================

def test_a_winner_pinned_by_a_cap_is_reported_as_on_the_boundary():
    """The live case: the search wants more than the cap allows, so it stops at
    the cap and the top rows all share that coordinate."""
    out = sp.search(_measure_factory([100.0, 100.0], cap=40.0), n=2, max_total=97.0)
    assert out["ok"]
    assert out["best"]["split_pct"] == [40.0, 40.0]
    b = out["boundary"]
    assert b["on_boundary"] is True
    assert b["blocked_neighbours"] > 0
    assert "clipped rather than optimised" in b["note"]


def test_an_interior_winner_says_no_constraint_is_choosing():
    """The other half of the claim. Without this the block would only ever say
    one thing, which is not a measurement."""
    out = sp.search(_measure_factory([20.0, 20.0]), n=2, max_total=97.0)
    b = out["boundary"]
    assert b["on_boundary"] is False
    assert b["blocked_neighbours"] == 0
    assert "interior" in b["note"]


def test_a_winner_using_the_whole_investable_share_is_on_the_boundary_too():
    """Nothing was inadmissible -- the cash floor simply left no room. A
    different reason, and one the note has to distinguish."""
    out = sp.search(_measure_factory([100.0, 100.0]), n=2, max_total=60.0)
    b = out["boundary"]
    assert b["at_max_total"] is True
    assert b["on_boundary"] is True
    assert "investable share" in b["note"]


def test_the_split_search_never_names_the_constraint_itself():
    """`split_solver` is handed a `measure` callable and knows nothing about
    caps or ceilings. Naming WHICH constraint binds is the caller's job, because
    only the caller knows what it enforced -- and a module that guessed would be
    a second opinion about a rule it cannot see."""
    out = sp.search(_measure_factory([100.0, 100.0], cap=40.0), n=2, max_total=97.0)
    assert "binding_constraint" not in out
    assert "cap" not in out["boundary"]["note"].lower()


def test_the_boundary_block_is_present_on_every_successful_search():
    """A field present on some outcomes is a field no renderer can rely on."""
    for best, cap, total in ([100.0, 100.0], 40.0, 97.0), ([20.0, 20.0], None, 97.0):
        out = sp.search(_measure_factory(best, cap=cap), n=2, max_total=total)
        assert "boundary" in out
        assert set(out["boundary"]) >= {"on_boundary", "at_max_total",
                                        "blocked_neighbours", "probed_neighbours",
                                        "note"}


# ===========================================================================
# the caller names the constraint
# ===========================================================================

def test_the_solver_names_the_cap_when_the_cap_is_what_stopped_it():
    import inspect

    from app.services import target_solver as T

    src = inspect.getsource(T.solve_split)
    assert "concentration_cap" in src
    assert "cash_floor" in src
    assert "drawdown_ceiling" in src
    # And it only names one when the search actually stopped on a boundary.
    assert 'found.get("boundary", {}).get("on_boundary")' in src


# ===========================================================================
# two questions, said out loud
# ===========================================================================

def test_both_searches_declare_which_question_they_answer():
    import inspect

    from app.services import target_solver as T

    t2 = inspect.getsource(T.solve)
    t6a = inspect.getsource(T.solve_split)
    assert "smallest_share_that_reaches_target" in t2
    assert "largest_excess_within_ceiling" in t6a
    # Each points at the other, so a reader holding one number is told the other
    # exists and why it differs.
    assert "differs_from" in t2 and "differs_from" in t6a


def test_neither_question_block_calls_the_other_wrong():
    """They are different questions, not a right and a wrong answer. A card
    that implied otherwise would be picking for the user."""
    import inspect

    from app.services import target_solver as T

    for src in (inspect.getsource(T.solve), inspect.getsource(T.solve_split)):
        assert "Neither is wrong" in src


@pytest.mark.parametrize("field", ["asks", "text", "differs_from"])
def test_the_question_block_is_complete(field):
    import inspect

    from app.services import target_solver as T

    assert f'"{field}"' in inspect.getsource(T.solve_split)
