"""T6b - the sleeve count is a measurement, and the list must not be actionable.

Two load-bearing tests:

  test_the_count_stops_where_the_gain_stops        - the count is discovered
  test_a_limit_that_binds_says_so_rather_than
      _reading_as_a_finding                        - and it never lies about why

The plan's words: "Discover the sleeve count, don't cap it... report where it
stopped and by how much the last one helped. The count is then a measurement
rather than a number someone picked. Keep a hard runtime ceiling as a backstop,
and log it if the backstop is what stopped the search -- a limit that binds
silently reads as a finding."
"""
from __future__ import annotations

from app.services import candidate_search as cs


def _by_value(values, dd=20.0, admissible=None):
    """measure() scoring a set by summing per-id contributions."""
    def measure(ids):
        if admissible is not None and not admissible(ids):
            return {"score_pct": 0.0, "max_drawdown_pct": 99.0, "admissible": False}
        return {"score_pct": round(sum(values.get(i, 0.0) for i in ids), 4),
                "max_drawdown_pct": dd, "admissible": True}
    return measure


# ------------------------------------------------ the count is discovered
def test_the_count_stops_where_the_gain_stops():
    """a=+5, b=+3, then c and d are worth 0.05 each -- inside the noise floor.
    The measured answer is TWO sleeves, and it must say that is why."""
    vals = {"a": 5.0, "b": 3.0, "c": 0.05, "d": 0.05}
    out = cs.grow(_by_value(vals), candidates=list(vals))
    assert out["chosen"] == ["a", "b"]
    assert out["n_sleeves"] == 2
    assert out["stopped"]["reason"] == "no_further_gain"
    assert out["stopped"]["is_a_limit"] is False
    assert "did not help" in out["stopped"]["detail"]


def test_the_marginal_gain_of_every_addition_is_reported():
    vals = {"a": 5.0, "b": 3.0}
    out = cs.grow(_by_value(vals), candidates=list(vals))
    assert [s["added"] for s in out["steps"]] == ["a", "b"]
    assert [s["marginal_gain_pct"] for s in out["steps"]] == [5.0, 3.0]
    assert out["last_addition_gain_pct"] == 3.0


def test_the_rejected_candidate_and_its_gain_are_named():
    """'It stopped at 2' is a number. 'The third was worth +0.05%/yr' is a
    reason, and it is what tells you whether to raise the floor."""
    vals = {"a": 5.0, "b": 3.0, "c": 0.05}
    out = cs.grow(_by_value(vals), candidates=list(vals))
    assert out["stopped"]["rejected"]["id"] == "c"
    assert out["stopped"]["rejected"]["gain_pct"] == 0.05


def test_a_negative_addition_is_not_taken():
    vals = {"a": 5.0, "b": -2.0}
    out = cs.grow(_by_value(vals), candidates=list(vals))
    assert out["chosen"] == ["a"]


# --------------------------------------- a limit must never read as a finding
def test_a_limit_that_binds_says_so_rather_than_reading_as_a_finding():
    """Every candidate helps a lot, so the search is stopped by the CAP, not by
    the measurement. Reporting '3 sleeves' without that distinction would be a
    number someone picked, presented as something that was discovered."""
    vals = {c: 5.0 for c in "abcdef"}
    out = cs.grow(_by_value(vals), candidates=list(vals), max_sleeves=3)
    assert out["n_sleeves"] == 3
    assert out["stopped"]["reason"] == "sleeve_cap"
    assert out["stopped"]["is_a_limit"] is True
    assert "not a finding" in out["stopped"]["detail"]
    assert "raise it to see further" in out["stopped"]["detail"]


def test_the_evaluation_budget_also_declares_itself():
    vals = {c: 5.0 for c in "abcdefghij"}
    out = cs.grow(_by_value(vals), candidates=list(vals), max_evaluations=4)
    assert out["stopped"]["is_a_limit"] is True
    assert out["stopped"]["reason"] in ("evaluation_budget", "sleeve_cap")


def test_exhausting_the_candidates_is_not_a_limit():
    vals = {"a": 5.0, "b": 3.0}
    out = cs.grow(_by_value(vals), candidates=list(vals), max_sleeves=9)
    assert out["stopped"]["reason"] == "candidates_exhausted"
    assert out["stopped"]["is_a_limit"] is False


def test_nothing_admissible_is_an_answer_not_a_crash():
    out = cs.grow(_by_value({"a": 5.0}, admissible=lambda ids: False),
                  candidates=["a"])
    assert out["stopped"]["reason"] == "nothing_admissible"
    assert out["chosen"] == []


def test_an_inadmissible_candidate_is_skipped_not_chosen():
    """A blend that breaches the ceiling is not eligible however well it scores."""
    vals = {"a": 5.0, "b": 100.0}
    out = cs.grow(_by_value(vals, admissible=lambda ids: "b" not in ids),
                  candidates=list(vals))
    assert "b" not in out["chosen"]


# ------------------------------------------------ honesty about the method
def test_the_payload_admits_greedy_can_miss_a_pair():
    out = cs.grow(_by_value({"a": 5.0}), candidates=["a"])
    assert "NOT exhaustive" in out["coverage"]["method"]
    assert "only works together" in out["coverage"]["method"]


def test_the_evaluation_count_is_reported():
    vals = {"a": 5.0, "b": 3.0, "c": 1.0}
    out = cs.grow(_by_value(vals), candidates=list(vals))
    assert out["coverage"]["evaluations"] > 0
    assert out["coverage"]["candidates_offered"] == 3


def test_a_set_is_never_measured_twice():
    """Greedy re-visits the same subsets constantly; each is a full simulation
    over ten years, so memoisation is the difference between seconds and
    minutes."""
    calls = []
    def measure(ids):
        calls.append(tuple(sorted(ids)))
        return {"score_pct": float(len(ids)), "max_drawdown_pct": 10.0, "admissible": True}
    cs.grow(measure, candidates=["a", "b", "c"])
    assert len(calls) == len(set(calls)), "the same sleeve set was simulated twice"


# ------------------------------------- no auto-apply, ever, from a ranked list
def test_a_ranked_list_forbids_auto_apply_in_the_payload():
    """The plan's rule. A single solve has one answer and can carry an Accept
    button; picking row 1 of a list searched over one history cannot."""
    out = cs.grow(_by_value({"a": 5.0, "b": 3.0}), candidates=["a", "b"])
    assert out["auto_apply_forbidden"] is True
    assert "selection on one sample" in out["auto_apply_note"]


def test_the_ranked_list_is_ordered_best_first():
    vals = {"a": 5.0, "b": 3.0, "c": 1.0}
    out = cs.grow(_by_value(vals), candidates=list(vals))
    scores = [r["score_pct"] for r in out["ranked"]]
    assert scores == sorted(scores, reverse=True)
    assert all("max_drawdown_pct" in r for r in out["ranked"]), \
        "ranking on return alone surfaces exactly the blends that are unholdable"


# --------------------------------------------------------------- the firewall
def test_no_security_can_enter_the_search():
    """investing-discipline section 5: the app never recommends products. The
    search sees strategy IDS only -- so a ticker cannot enter by construction,
    and this makes that catchable rather than merely true today."""
    allowed = {"btm_trend_soxl", "btm_swing_dip", "btm_factor_stack"}
    out = cs.grow(_by_value({k: 3.0 for k in allowed}), candidates=sorted(allowed))
    assert cs.forbidden_tickers(out, allowed) == []


def test_an_id_that_was_never_offered_is_caught():
    allowed = {"btm_trend_soxl"}
    out = cs.grow(_by_value({"btm_trend_soxl": 3.0, "SOXL": 9.0}),
                  candidates=["btm_trend_soxl", "SOXL"])
    assert "SOXL" in cs.forbidden_tickers(out, allowed)
