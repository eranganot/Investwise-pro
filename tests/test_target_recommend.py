"""T5 - the card has to end in an instruction, and the instruction has to be right.

The verdicts here are built from the numbers on the real card (screenshot,
2026-08-16): -2.88%/yr over SPY, 31.89% drawdown, 13.3% ceiling, sleeves solved
to 0%, excess at equal risk -2.07%/yr. That case is the one that read as a dead
end, so it is the one the tests are anchored to.

The failure this file guards against is subtle and expensive: a DRAWDOWN_BOUND
whose floor already breaches the ceiling means the CORE is the constraint, and
telling the reader to resize sleeves there is worse than saying nothing -- it
sends them to a control that provably cannot help.
"""
from __future__ import annotations

import pytest

from app.services import target_solver as ts


# --------------------------------------------------------------- the real card
def _his_verdict():
    """DRAWDOWN_BOUND, sleeves at 0%, core alone already over the ceiling."""
    return {
        "outcome": ts.DRAWDOWN_BOUND,
        "benchmark": "SPY",
        "target": {"excess_pct": -3.1, "max_drawdown_pct": 13.3},
        "solved_total_sleeve_pct": 0.0,
        "display_total_sleeve_pct": 0.0,
        "binding_constraint": {"kind": "drawdown_ceiling",
                               "ceiling_pct": 13.3, "would_require_pct": 31.89},
        "floor": {"at_zero_sleeve_pct": 0.0, "max_drawdown_pct": 31.89,
                  "breaches_ceiling": True, "note": "..."},
        "best_within_ceiling": None,
        "measured": {"cagr_pct": 10.5, "max_drawdown_pct": 31.89,
                     "excess_cagr_pct": -2.88, "benchmark_cagr_pct": 13.38,
                     "benchmark_max_drawdown_pct": 34.1,
                     "excess_at_equal_risk_pct": -2.07,
                     "equal_risk_leverage": 1.079},
    }


def test_it_does_not_send_him_back_to_the_sleeve_slider():
    """The core is 97% of the book and already over the ceiling. Sleeve advice
    here would point at a control that cannot move the outcome."""
    r = ts.recommend(_his_verdict(), benchmark="SPY")
    assert not any(a["kind"] == "set_sleeves" for a in r["actions"])
    assert "sleeves are not the problem" in r["headline"].lower()


def test_it_names_the_ceiling_that_would_unblock_the_solve():
    r = ts.recommend(_his_verdict(), benchmark="SPY")
    acts = [a for a in r["actions"] if a["kind"] == "set_ceiling"]
    assert len(acts) == 1
    # 31.89 measured -> 32. Rounded UP: a ceiling below what the core already
    # does would come straight back as DRAWDOWN_BOUND again.
    assert acts[0]["value"] == 32.0
    assert acts[0]["value"] >= _his_verdict()["floor"]["max_drawdown_pct"]


def test_a_raised_ceiling_is_described_as_a_permission_not_a_return():
    """The one way this feature could actively mislead: implying that lifting a
    risk ceiling produces return. It does not. It removes a refusal."""
    r = ts.recommend(_his_verdict(), benchmark="SPY")
    text = " ".join(a["detail"] for a in r["actions"]).lower()
    assert "permission, not a return" in text
    assert "does not add a single percent" in text


def test_the_equal_risk_finding_outranks_the_solve_and_is_always_said():
    r = ts.recommend(_his_verdict(), benchmark="SPY")
    w = r["equal_risk_warning"]
    assert w is not None
    assert "2.07" in w and "BEHIND" in w
    assert "no sleeve size repairs it" in w.lower()


def test_a_positive_equal_risk_excess_raises_no_warning():
    v = _his_verdict()
    v["measured"]["excess_at_equal_risk_pct"] = 1.4
    assert ts.recommend(v, benchmark="SPY")["equal_risk_warning"] is None


def test_a_missing_equal_risk_figure_is_silent_rather_than_assumed_bad():
    v = _his_verdict()
    v["measured"]["excess_at_equal_risk_pct"] = None
    assert ts.recommend(v, benchmark="SPY")["equal_risk_warning"] is None


def test_the_core_case_is_flagged_bad_not_merely_warned():
    assert ts.recommend(_his_verdict(), benchmark="SPY")["severity"] == "bad"


# ------------------------------------- drawdown-bound where sleeves DO bind
def _sleeve_bound():
    v = _his_verdict()
    v["target"] = {"excess_pct": 8.0, "max_drawdown_pct": 25.0}
    v["binding_constraint"] = {"kind": "drawdown_ceiling",
                               "ceiling_pct": 25.0, "would_require_pct": 41.2}
    v["floor"] = {"at_zero_sleeve_pct": 0.0, "max_drawdown_pct": 18.0,
                  "breaches_ceiling": False, "note": None}
    v["best_within_ceiling"] = {"total_sleeve_pct": 20.0, "excess_pct": 3.4,
                                "max_drawdown_pct": 24.1}
    return v


def test_when_the_sleeves_really_do_bind_it_offers_both_ways_out():
    r = ts.recommend(_sleeve_bound(), benchmark="SPY")
    kinds = {a["kind"] for a in r["actions"]}
    assert kinds == {"set_ceiling", "set_target"}
    assert "sleeves are not the problem" not in r["headline"].lower()


def test_the_smaller_target_offered_is_one_that_was_actually_measured():
    r = ts.recommend(_sleeve_bound(), benchmark="SPY")
    alt = [a for a in r["actions"] if a["kind"] == "set_target"][0]
    assert alt["value"] == 3.4          # best_within_ceiling, not invented
    assert "20" in alt["detail"]


def test_the_ceiling_offered_clears_what_the_target_would_require():
    r = ts.recommend(_sleeve_bound(), benchmark="SPY")
    act = [a for a in r["actions"] if a["kind"] == "set_ceiling"][0]
    assert act["value"] == 42.0 and act["value"] >= 41.2


# -------------------------------------------------------------------- reached
def _reached():
    return {
        "outcome": ts.REACHED, "benchmark": "SPY",
        "target": {"excess_pct": 5.0, "max_drawdown_pct": 50.0},
        "solved_total_sleeve_pct": 23.0, "display_total_sleeve_pct": 25.0,
        "measured": {"max_drawdown_pct": 44.2, "excess_cagr_pct": 5.3,
                     "excess_at_equal_risk_pct": 2.1},
    }


def test_a_reached_solve_gives_a_size_and_says_it_is_the_smallest():
    r = ts.recommend(_reached(), benchmark="SPY")
    act = [a for a in r["actions"] if a["kind"] == "set_sleeves"][0]
    # The DISPLAY size, not the solved one: the card shows 25 and the
    # instruction must not tell him to type a number the card never showed.
    assert act["value"] == 25.0
    assert "smallest" in r["because"].lower()
    assert r["severity"] == "ok"


def test_the_action_never_promises_to_place_an_order():
    """The firewall: this app produces an instruction, never a trade."""
    act = [a for a in ts.recommend(_reached())["actions"] if a["kind"] == "set_sleeves"][0]
    assert "never places an order" in act["detail"]


# --------------------------------------------------------------- unreachable
def test_unreachable_points_at_the_sleeve_to_fix_not_at_the_slider():
    v = {
        "outcome": ts.UNREACHABLE, "benchmark": "SPY",
        "target": {"excess_pct": 16.0, "max_drawdown_pct": 50.0},
        "display_total_sleeve_pct": 100.0,
        "binding_constraint": {"kind": "component_excess", "component": "swing_dipbuy",
                               "component_cagr_pct": 4.2, "benchmark_cagr_pct": 13.4,
                               "component_excess_pct": -9.2},
        "measured": {"excess_cagr_pct": 1.1, "excess_at_equal_risk_pct": 0.4},
    }
    r = ts.recommend(v, benchmark="SPY")
    assert "swing_dipbuy" in r["because"]
    assert "rule to fix or replace, not a slider to drag" in r["because"]
    assert [a for a in r["actions"] if a["kind"] == "set_target"][0]["value"] == 1.1


def test_unreachable_with_nothing_admissible_blames_the_catalog():
    v = {"outcome": ts.UNREACHABLE, "benchmark": "SPY",
         "target": {"excess_pct": 16.0, "max_drawdown_pct": 50.0},
         "display_total_sleeve_pct": 0.0, "measured": {}}
    r = ts.recommend(v, benchmark="SPY")
    assert "catalog" in r["headline"].lower()
    assert r["actions"] == []


# ------------------------------------------------------------- above the cap
def test_above_cap_names_the_ticker_and_the_rule_the_user_set():
    v = {"outcome": ts.REACHED_ABOVE_CAP, "benchmark": "SPY",
         "target": {"excess_pct": 6.0, "max_drawdown_pct": 50.0},
         "display_total_sleeve_pct": 40.0,
         "binding_constraint": {"kind": "concentration_cap", "cap_pct": 25.0,
                                "ticker": "SOXL", "would_reach_pct": 38.0},
         "measured": {"excess_at_equal_risk_pct": 1.0}}
    r = ts.recommend(v, benchmark="SPY")
    assert "SOXL" in r["because"] and "38" in r["because"]
    assert "a rule you set, not a measurement" in r["because"]


# ------------------------------------------------------------ not measurable
def test_no_sleeves_says_add_one_rather_than_returning_an_empty_card():
    v = {"outcome": ts.NOT_MEASURABLE, "reason": "NO_SLEEVES", "target": {}}
    r = ts.recommend(v)
    assert "Add a strategy" in r["because"]
    assert r["actions"] == []


def test_every_outcome_produces_a_headline_and_a_reason():
    """No outcome may render a blank instruction -- that is the bug T5 fixes."""
    for v in (_his_verdict(), _sleeve_bound(), _reached(),
              {"outcome": ts.NOT_MEASURABLE, "reason": "NO_SLEEVES", "target": {}},
              {"outcome": "SOMETHING_NEW", "target": {}}):
        r = ts.recommend(v, benchmark="SPY")
        assert r["headline"].strip() and r["because"].strip()
        assert r["severity"] in {"ok", "warn", "bad"}


def test_the_recommendation_invents_no_figure_the_solve_did_not_measure():
    """Every number in the text must trace to the verdict it was given."""
    v = _his_verdict()
    r = ts.recommend(v, benchmark="SPY")
    blob = r["headline"] + r["because"] + " ".join(a["detail"] for a in r["actions"])
    import re
    nums = {float(x) for x in re.findall(r"\d+\.?\d*", blob)}
    allowed = {13.3, 31.89, 32.0, 2.07, 3.1, 2.88}
    assert nums <= allowed, f"unexplained figures: {sorted(nums - allowed)}"


# ---------------------------------------------------- totality (caught in T5)
# recommend() runs at the END of every solve. An instruction is decoration on
# top of a measurement, so if a missing figure can raise here, a solve that
# measured perfectly well returns a 500 and the user loses the whole card --
# strictly worse than the missing instruction T5 exists to add. Every one of
# these raised TypeError on the first draft.
@pytest.mark.parametrize("outcome", [
    ts.REACHED, ts.REACHED_ABOVE_CAP, ts.DRAWDOWN_BOUND,
    ts.UNREACHABLE, ts.NOT_MEASURABLE, "SOMETHING_UNRECOGNISED",
])
def test_it_never_raises_on_an_empty_verdict(outcome):
    r = ts.recommend({"outcome": outcome, "target": {}, "measured": {}})
    assert r["headline"].strip()
    assert r["severity"] in {"ok", "warn", "bad"}


def test_a_missing_figure_renders_as_unknown_rather_than_crashing():
    r = ts.recommend({"outcome": ts.REACHED, "target": {}, "measured": {}})
    assert "?" in r["headline"]


def test_an_action_with_no_computable_value_is_dropped_not_disabled():
    """A greyed-out button still says 'there is a lever here'. There isn't."""
    v = {"outcome": ts.DRAWDOWN_BOUND, "target": {"max_drawdown_pct": 13.3},
         "binding_constraint": {"kind": "drawdown_ceiling", "would_require_pct": None},
         "floor": {"max_drawdown_pct": None, "breaches_ceiling": True},
         "measured": {}}
    assert [a for a in ts.recommend(v)["actions"] if a["kind"] == "set_ceiling"] == []


def test_the_ceiling_rounds_up_never_down():
    # Down would hand back a ceiling the book already breaches, so the one-tap
    # would be a button that provably changes nothing.
    assert ts._ceil_pct(31.89) == 32.0
    assert ts._ceil_pct(31.0) == 31.0
    assert ts._ceil_pct(41.2) == 42.0
    assert ts._ceil_pct(None) is None


def test_the_component_excess_keeps_its_sign():
    v = {"outcome": ts.UNREACHABLE, "target": {"excess_pct": 16.0, "max_drawdown_pct": 50.0},
         "binding_constraint": {"kind": "component_excess", "component": "x",
                                "component_cagr_pct": 4.2, "benchmark_cagr_pct": 13.4,
                                "component_excess_pct": -9.2},
         "measured": {"excess_cagr_pct": 1.1}}
    assert "-9.2%/yr" in ts.recommend(v)["because"]
