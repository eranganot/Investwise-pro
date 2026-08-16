"""Phase A - accepting a recommendation must not leave the book half-changed.

The load-bearing test here is `test_a_raise_before_a_drop_would_half_apply`.
Everything else guards its edges.

`sleeve_service.add_or_resize` refuses rather than half-applies, which is right
per call and insufficient across several: applying (40,40) -> (70,20) in the
order the solve happens to list them raises the first sleeve to 70% while the
second still holds 40%, the total hits 110%, `validate` refuses, and the book is
left at (40,20) -- neither plan, and no error the user asked for. Nothing in the
codebase catches that, because each individual call behaved correctly.
"""
from __future__ import annotations

from app.services import target_apply as ta


def _r(sid, frm, to):
    return {"strategy_id": sid, "from_pct": frm, "to_pct": to}


# ------------------------------------------------------------- the whole point
def test_a_raise_before_a_drop_would_half_apply():
    """(40,40) -> (70,20). Raising first passes through 110%."""
    plan = ta.plan_apply([_r("a", 40.0, 70.0), _r("b", 40.0, 20.0)])
    assert plan["ok"]
    # b (the decrease) must be written first, whatever order the solve listed.
    assert [s["strategy_id"] for s in plan["steps"]] == ["b", "a"]
    # And the running total never exceeds the ceiling at any point.
    assert all(s["book_total_after_pct"] <= 100.0 + ta.EPSILON_PCT
               for s in plan["steps"])


def test_the_running_total_is_recorded_at_every_step_not_just_the_end():
    plan = ta.plan_apply([_r("a", 40.0, 70.0), _r("b", 40.0, 20.0)])
    assert [s["book_total_after_pct"] for s in plan["steps"]] == [60.0, 90.0]


def test_the_solves_own_order_is_not_trusted():
    """Same plan, listed the other way round, must produce the same write order."""
    a = ta.plan_apply([_r("a", 40.0, 70.0), _r("b", 40.0, 20.0)])
    b = ta.plan_apply([_r("b", 40.0, 20.0), _r("a", 40.0, 70.0)])
    assert [s["strategy_id"] for s in a["steps"]] == [s["strategy_id"] for s in b["steps"]]


# ------------------------------------------------------------ the real card
def test_his_actual_65_percent_plan():
    """Trend-Filtered Semiconductors 25.6 -> 26.0, Swing Dip-Buy 38.4 -> 39.0."""
    plan = ta.plan_apply([_r("btm_trend_soxl", 25.6, 26.0),
                          _r("btm_swing_dip", 38.4, 39.0)])
    assert plan["ok"]
    assert plan["final_total_pct"] == 65.0
    assert plan["core_pct_after"] == 35.0
    assert len(plan["changed"]) == 2


# ----------------------------------------------------------------- refusals
def test_a_plan_that_does_not_fit_is_refused_whole():
    plan = ta.plan_apply([_r("a", 10.0, 60.0), _r("b", 10.0, 60.0)])
    assert not plan["ok"]
    assert "does not fit" in plan["reason"]
    assert "Nothing was written" in plan["detail"]
    assert "steps" not in plan          # no partial plan is ever handed back


def test_the_cash_floor_lowers_the_ceiling():
    ok = ta.plan_apply([_r("a", 10.0, 95.0)], cash_floor_pct=0.0)
    assert ok["ok"]
    no = ta.plan_apply([_r("a", 10.0, 95.0)], cash_floor_pct=10.0)
    assert not no["ok"] and no["ceiling_pct"] == 90.0


def test_an_empty_plan_is_a_refusal_not_a_silent_success():
    assert not ta.plan_apply([])["ok"]
    assert not ta.plan_apply(None)["ok"]


def test_a_duplicate_strategy_in_one_plan_is_refused():
    plan = ta.plan_apply([_r("a", 10.0, 20.0), _r("a", 10.0, 30.0)])
    assert not plan["ok"] and "twice" in plan["reason"]


def test_a_negative_size_is_refused():
    assert not ta.plan_apply([_r("a", 10.0, -5.0)])["ok"]


def test_a_size_that_is_not_a_number_is_refused_rather_than_coerced():
    assert not ta.plan_apply([{"strategy_id": "a", "from_pct": 1, "to_pct": "lots"}])["ok"]


def test_a_step_with_no_strategy_is_refused():
    assert not ta.plan_apply([{"strategy_id": "", "from_pct": 1, "to_pct": 2}])["ok"]


# ------------------------------------------------------------------- shapes
def test_going_to_zero_is_a_removal_not_a_resize():
    """sleeve_service.validate refuses 0 outright ('0% is a removal, not a
    size'), so routing it through add_or_resize would fail the whole apply."""
    plan = ta.plan_apply([_r("a", 20.0, 0.0), _r("b", 20.0, 30.0)])
    assert plan["ok"]
    assert [s["op"] for s in plan["steps"]] == ["remove", "resize"]


def test_an_unchanged_sleeve_is_not_written():
    plan = ta.plan_apply([_r("a", 20.0, 20.0), _r("b", 20.0, 30.0)])
    assert plan["unchanged"] == ["a"]
    assert [s["strategy_id"] for s in plan["changed"]] == ["b"]


def test_increases_are_applied_smallest_first():
    plan = ta.plan_apply([_r("a", 1.0, 30.0), _r("b", 1.0, 10.0), _r("c", 1.0, 20.0)])
    assert [s["strategy_id"] for s in plan["steps"]] == ["b", "c", "a"]


# -------------------------------------------------------------- staleness
def test_a_plan_solved_against_a_different_book_is_detected():
    """The quietest failure: the card's numbers describe a book that has since
    changed, so 'accepting' silently overwrites the current plan instead."""
    stale = ta.stale_against([_r("a", 25.6, 26.0)], {"a": 30.0})
    assert len(stale) == 1
    assert stale[0]["solved_against_pct"] == 25.6
    assert stale[0]["book_now_pct"] == 30.0


def test_a_book_that_has_not_moved_is_not_flagged():
    assert ta.stale_against([_r("a", 25.6, 26.0)], {"a": 25.6}) == []


def test_a_sleeve_that_vanished_from_the_book_is_stale():
    assert len(ta.stale_against([_r("a", 25.6, 26.0)], {})) == 1


def test_rounding_noise_is_not_treated_as_a_change():
    # sleeve_service.validate allows 0.05 of slack; the same figure is used
    # here so a plan this module admits is never refused one layer down.
    assert ta.stale_against([_r("a", 25.60, 26.0)], {"a": 25.62}) == []


def test_a_new_sleeve_starting_from_zero_is_not_stale():
    assert ta.stale_against([_r("a", 0.0, 20.0)], {}) == []


# ------------------------------------------------- the firewall, in one test
def test_nothing_in_the_planner_produces_an_order():
    """investing-discipline section 5: this app never places an order. The
    planner deals in percentages of a tracked book -- no quantity, no price,
    no ticker, no side."""
    plan = ta.plan_apply([_r("a", 10.0, 20.0)])
    blob = repr(plan).lower()
    for forbidden in ("quantity", "limit_price", "order_type", '"buy"', '"sell"'):
        assert forbidden not in blob


# ------------------------------------------- the firewall, structurally
def test_the_apply_module_cannot_reach_a_broker():
    """`investing-discipline` section 5: this app never places an order.

    Asserted on the IMPORT GRAPH, not on a text search for the word "order" --
    this module's own docstrings say "places an order" several times, in
    sentences denying that it does. A grep-based version of this check fails on
    its own safety notes, which is how a check gets deleted for being noisy and
    stops protecting anything. (The first draft of this test did exactly that.)

    Exact (module, name) pairs, not module prefixes: allowing `app.services`
    wholesale would permit importing anything under it, which is most of the
    app. A module that imports nothing capable of execution cannot execute, and
    extending this list is meant to be the moment someone stops and thinks.
    """
    import ast

    allowed = {
        ("logging", None),
        ("__future__", "annotations"),
        ("app.services", "sleeve_service"),
        ("app.services.funding_service", "cash_floor_pct"),
        ("sqlalchemy", "select"),
        ("app.models.tables", "PlanApplication"),
    }
    src = open("app/services/target_apply.py", encoding="utf-8").read()
    found = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            found |= {(a.name, None) for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found |= {(node.module or "", a.name) for a in node.names}
    extra = found - allowed
    assert not extra, f"target_apply imports something new: {sorted(extra)}"


def test_the_planner_writes_nothing_by_itself():
    """`plan_apply` and `stale_against` are pure: no session, no await, no ORM.

    Read from the FILE, not via `inspect.getsource`. getsource needs the module
    to have been imported from disk with its source resolvable, which is an
    assumption about how the tests are run rather than about the code -- and it
    is the same assumption that made a ship-t5 check fail for a reason that had
    nothing to do with the thing being checked.

    The ordering rule is the only thing between an accepted recommendation and a
    half-rewritten book, so it has to be verifiable without a database --
    otherwise the most important logic in the phase is also the least tested.
    """
    import re

    src = open("app/services/target_apply.py", encoding="utf-8").read()
    for name in ("def plan_apply(", "def stale_against("):
        body = src[src.index(name):]
        # `\ndef ` alone under-terminates: the function after `stale_against` is
        # `async def apply_target`, so the slice ran on through the entire
        # writing half and the test failed on code it was never about. Match
        # both forms.
        m = re.search(r"\n(?:async )?def ", body[1:])
        body = body[:m.start() + 1] if m else body
        assert "await " not in body, f"{name} awaits something"
        assert "session" not in body, f"{name} touches a session"
