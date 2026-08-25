"""PR 13b / T6b second half -- discover the sleeve count, don't pick a number.

The plan's bullet, verbatim:

    "Discover the sleeve count, don't cap it. Grow the number of concurrent
     sleeves while each addition improves the constrained objective; stop when
     it stops, and report where it stopped and by how much the last one helped.
     The count is then a measurement rather than a number someone picked. Keep
     a hard runtime ceiling as a backstop, and log it if the backstop is what
     stopped the search -- a limit that binds silently reads as a finding."

T2 resizes a FIXED set; T6a sizes each member of that fixed set on its own axis.
Neither can answer "should there be a third one at all", because both take the
membership as given -- and this book runs two sleeves for no measured reason.

The tests below are mostly about the REPORT rather than the algorithm. Greedy
forward selection is four lines; what makes the answer usable is that "it
stopped at two" can be checked rather than trusted.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.services import core_search as cs  # noqa: E402
from app.services import sleeve_count as sc  # noqa: E402
from app.services import split_solver as ss  # noqa: E402

CANDS = [{"id": f"s{i}", "name": f"S{i}"} for i in range(1, 6)]


def _mk(gains: dict, *, dd=-5.0, dd_per=1.0):
    """A measure whose excess is the sum of each member's fixed contribution."""
    def measure(ids):
        ex = sum(gains.get(i, 0.0) for i in ids)
        return {"ok": True, "excess_cagr_pct": ex, "cagr_pct": ex + 8.0,
                "max_drawdown_pct": dd - dd_per * len(ids), "observations": 2513}
    return measure


# ===========================================================================
# it stops where the improvement stops
# ===========================================================================

def test_the_count_is_measured_not_chosen():
    out = sc.discover(CANDS, _mk({"s1": 3.0, "s2": 2.0, "s3": 0.1, "s4": 0.05, "s5": 0.02}),
                      max_drawdown_pct=99.0)
    assert out["chosen"] == ["s1", "s2"]
    assert out["count"] == 2
    assert out["stopped_because"] == sc.BELOW_NOISE_FLOOR
    assert out["converged"] is True


def test_it_reports_by_how_much_the_last_one_helped():
    """"It stopped at two" is not a finding. "The best third added 0.1% against a
    0.25% floor" is, and it is the row that lets a reader disagree with the
    floor."""
    out = sc.discover(CANDS, _mk({"s1": 3.0, "s2": 2.0, "s3": 0.1}), max_drawdown_pct=99.0)
    assert out["last_gain_pct"] == 0.1
    assert out["noise_floor_pct"] == sc.NOISE_FLOOR_PCT
    final = out["rounds"][-1]
    assert final["added"] is None
    assert final["best_rejected"] == "s3"
    assert final["gain_pct"] == 0.1


def test_every_round_records_what_it_rejected():
    """A round that reports only its winner cannot be checked."""
    out = sc.discover(CANDS, _mk({"s1": 3.0, "s2": 2.0}), max_drawdown_pct=99.0)
    first = out["rounds"][0]
    assert {t["id"] for t in first["tried"]} == {"s1", "s2", "s3", "s4", "s5"}
    assert all("gain_pct" in t for t in first["tried"])


def test_a_sleeve_that_helps_a_lot_is_still_added_after_a_weak_one():
    """Greedy takes the BEST addition each round, not the first admissible one."""
    out = sc.discover(CANDS, _mk({"s5": 9.0, "s1": 1.0}), max_drawdown_pct=99.0)
    assert out["chosen"][0] == "s5"


# ===========================================================================
# the ceiling constrains the objective, it is not reported afterwards
# ===========================================================================

def test_an_addition_that_breaches_the_ceiling_is_never_taken():
    """Ranking additions on return alone selects for exactly the sleeves that
    make a book unholdable -- every sleeve in this family is leveraged or
    concentrated."""
    def measure(ids):
        return {"ok": True, "excess_cagr_pct": 5.0 * len(ids), "cagr_pct": 13.0,
                "max_drawdown_pct": -40.0 if ids else -5.0, "observations": 2513}

    out = sc.discover(CANDS, measure, max_drawdown_pct=13.3)
    assert out["chosen"] == []
    assert out["count"] == 0
    assert out["stopped_because"] == sc.NO_ADMISSIBLE_ADDITION
    assert all(t["breaches_ceiling"] for t in out["rounds"][0]["tried"])


def test_zero_sleeves_is_a_legitimate_answer():
    """"No sleeves" is a sleeve count. A search that cannot return zero has
    assumed its own conclusion."""
    def measure(ids):
        return {"ok": True, "excess_cagr_pct": -1.0 * len(ids), "cagr_pct": 8.0,
                "max_drawdown_pct": -5.0, "observations": 2513}

    out = sc.discover(CANDS, measure, max_drawdown_pct=99.0)
    assert out["count"] == 0
    assert out["converged"] is True


def test_the_search_starts_from_the_set_the_book_already_runs():
    """"One more than you have" and "how many at all" are different questions,
    and the payload says which was asked."""
    out = sc.discover(CANDS, _mk({"s1": 3.0, "s2": 2.0, "s3": 5.0}),
                      max_drawdown_pct=99.0, start=["s1", "s2"])
    assert out["started_from"] == ["s1", "s2"]
    assert out["chosen"][:2] == ["s1", "s2"]
    assert "s3" in out["chosen"]


# ===========================================================================
# a limit that binds silently reads as a finding
# ===========================================================================

def test_the_runtime_backstop_is_reported_not_swallowed(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        out = sc.discover(CANDS, _mk({f"s{i}": 5.0 for i in range(1, 6)}),
                          max_drawdown_pct=99.0, max_measures=4)
    assert out["stopped_because"] == sc.RUNTIME_BACKSTOP
    assert out["coverage"]["runtime_backstop_hit"] is True
    assert "backstop" in caplog.text


def test_a_backstopped_count_is_a_floor_not_a_result():
    """The four ways this ends mean completely different things, and a payload
    reporting only the count would render them identically."""
    out = sc.discover(CANDS, _mk({f"s{i}": 5.0 for i in range(1, 6)}),
                      max_drawdown_pct=99.0, max_measures=4)
    assert out["converged"] is False
    assert out["count_is_a_floor"] is True


def test_running_out_of_candidates_is_not_convergence():
    out = sc.discover(CANDS, _mk({f"s{i}": 5.0 for i in range(1, 6)}),
                      max_drawdown_pct=99.0)
    assert out["stopped_because"] == sc.MAX_SLEEVES_REACHED
    assert out["converged"] is False
    assert out["count_is_a_floor"] is True


def test_an_interrupted_round_is_still_reported():
    """It is what the backstop interrupted; hiding it makes the budget
    invisible."""
    out = sc.discover(CANDS, _mk({f"s{i}": 5.0 for i in range(1, 6)}),
                      max_drawdown_pct=99.0, max_measures=3)
    assert any(r.get("partial") for r in out["rounds"])


def test_an_interrupted_round_never_claims_a_winner():
    """A round whose candidates were not all tried cannot support "this was the
    best addition"."""
    out = sc.discover(CANDS, _mk({f"s{i}": 5.0 for i in range(1, 6)}),
                      max_drawdown_pct=99.0, max_measures=3)
    partial = [r for r in out["rounds"] if r.get("partial")]
    assert partial and all(r["added"] is None for r in partial)


# ===========================================================================
# it does not pretend to be exhaustive
# ===========================================================================

def test_the_payload_admits_greedy_is_not_optimal():
    """A set this search rejects may still beat the set it keeps. Letting the
    word "discovered" imply otherwise is the lie of omission T2's own docstring
    warns about for the one-axis answer."""
    out = sc.discover(CANDS, _mk({"s1": 3.0}), max_drawdown_pct=99.0)
    assert out["method"]["kind"] == "greedy_forward_selection"
    assert "greedy" in out["method"]["caveat"]
    assert "127" in out["method"]["caveat"]


def test_an_unmeasurable_baseline_abstains_rather_than_returning_zero():
    """Returning count=0 from "could not measure" would be indistinguishable
    from the real finding that no sleeve earns its place."""
    out = sc.discover(CANDS, lambda ids: {"ok": False, "reason": "NO_OVERLAP",
                                          "detail": "no common dates"},
                      max_drawdown_pct=99.0)
    assert out["ok"] is False
    assert out["stopped_because"] == sc.NOTHING_MEASURABLE
    assert "NO_OVERLAP" in out["detail"]


def test_the_three_searches_agree_on_the_noise_floor():
    """Duplicated deliberately so they MAY diverge; asserted equal so a
    divergence is a decision rather than a drift."""
    assert sc.NOISE_FLOOR_PCT == cs.NOISE_FLOOR_PCT == ss.NOISE_FLOOR_PCT


def test_the_count_is_never_confounded_with_the_size():
    """A three-sleeve set that wins because the search also handed it more of
    the book has not shown that three beats two."""
    import inspect

    from app.services import target_solver

    src = inspect.getsource(target_solver._count_blocking)
    assert "total_pct / len(specs)" in src, "the split must be equal across sets"
    assert "separate questions" in src.lower()


@pytest.mark.parametrize("floor", [0.0, 0.25, 1.0, 5.0])
def test_a_higher_floor_never_yields_more_sleeves(floor):
    """The property, rather than one arrangement of it."""
    gains = {"s1": 3.0, "s2": 2.0, "s3": 0.9, "s4": 0.4, "s5": 0.1}
    out = sc.discover(CANDS, _mk(gains), max_drawdown_pct=99.0, noise_floor_pct=floor)
    loose = sc.discover(CANDS, _mk(gains), max_drawdown_pct=99.0, noise_floor_pct=0.0)
    assert out["count"] <= loose["count"]


# ===========================================================================
# the outcome the live run exposed
# ===========================================================================

def test_a_baseline_that_already_breaches_is_its_own_outcome():
    """FOUND BY RUNNING THIS AGAINST THE REAL BOOK. Every candidate third sleeve
    breached, so the search reported NO_ADMISSIBLE_ADDITION -- technically true
    and badly misleading, because a reader sees count=2 and concludes two is
    right. The measured truth was that the two already held blow the ceiling by
    31 points, which makes "which third improves it" the wrong question rather
    than one with no answer."""
    def measure(ids):
        return {"ok": True, "excess_cagr_pct": 13.08, "cagr_pct": 21.0,
                "max_drawdown_pct": -44.58, "observations": 2513}

    out = sc.discover(CANDS, measure, max_drawdown_pct=13.3, start=["s1", "s2"])
    assert out["stopped_because"] == sc.BASELINE_BREACHES_CEILING
    assert out["verdict"]["baseline_breaches_ceiling"] is True
    assert out["verdict"]["baseline_max_drawdown_pct"] == -44.58
    assert "44.6" in out["verdict"]["headline"]
    assert "13.3" in out["verdict"]["headline"]
    # And it must refuse to be read as a recommendation.
    assert "not a recommendation" in out["verdict"]["means"]


def test_a_breaching_baseline_does_not_waste_a_round_of_measurements():
    """Checked before the first round: every gain against an inadmissible
    baseline is None, so the rounds would measure the whole pool to learn
    nothing."""
    calls = {"n": 0}

    def measure(ids):
        calls["n"] += 1
        return {"ok": True, "excess_cagr_pct": 1.0, "cagr_pct": 9.0,
                "max_drawdown_pct": -44.0, "observations": 2513}

    sc.discover(CANDS, measure, max_drawdown_pct=13.3, start=["s1"])
    assert calls["n"] == 1, "only the baseline should have been measured"


def test_no_admissible_addition_still_means_the_held_set_is_fine():
    """The two outcomes must stay distinct: this one says "your set is
    admissible and nothing improves it", which IS a finding."""
    def measure(ids):
        breach = len(ids) > 2
        return {"ok": True, "excess_cagr_pct": 5.0,
                "cagr_pct": 13.0,
                "max_drawdown_pct": -40.0 if breach else -9.0,
                "observations": 2513}

    out = sc.discover(CANDS, measure, max_drawdown_pct=13.3, start=["s1", "s2"])
    assert out["stopped_because"] == sc.NO_ADMISSIBLE_ADDITION
    assert out["verdict"]["baseline_breaches_ceiling"] is False
    assert "not a budget limit" in out["verdict"]["means"]


def test_every_outcome_carries_a_headline_a_reader_can_act_on():
    """A bare count renders identically whether it is a finding or an artefact
    of a question that could not be asked."""
    cases = [
        (dict(max_drawdown_pct=99.0), _mk({"s1": 3.0, "s2": 0.1})),
        (dict(max_drawdown_pct=13.3, start=["s1"]),
         lambda ids: {"ok": True, "excess_cagr_pct": 1.0, "cagr_pct": 9.0,
                      "max_drawdown_pct": -44.0, "observations": 2513}),
        (dict(max_drawdown_pct=99.0, max_measures=3),
         _mk({f"s{i}": 5.0 for i in range(1, 6)})),
        (dict(max_drawdown_pct=99.0), _mk({f"s{i}": 5.0 for i in range(1, 6)})),
    ]
    for kwargs, measure in cases:
        out = sc.discover(CANDS, measure, **kwargs)
        v = out["verdict"]
        assert v["kind"] == out["stopped_because"]
        assert v["headline"] and v["means"], out["stopped_because"]
