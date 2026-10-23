"""D122 (owner, 2026-10-23): keep the halving limit, revisit the GOAL -- so the
solve prices a range of goals on the same rules replay as the headline.

Production 10-23: 30%/yr (₪82,692 by end-2031 from ₪19,670) is 0.2% likely with
the protective rules and 18.5% without them, halving 53.9%. The owner chose to
look at reachable goals rather than loosen protection.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402

from app.services import frontier_service as fs  # noqa: E402

TARGET, YEARS, ROI = 82691.86, 5.188, 0.30


def _fake_run(calls):
    def run(rules_on=True, years_=None, monthly_=None, target_=None, n_paths=None, **kw):
        calls.append({"rules_on": rules_on, "years": years_, "monthly": monthly_, "target": target_,
                      "paths": n_paths})
        # a book that reaches ~15%/yr: odds fall as the target rises
        p = max(0.0, min(100.0, 100.0 * (40000.0 / target_) ** 2 * (1.0 if rules_on else 1.5)))
        return {"p_goal_pct": round(p, 1), "p_halve_pct": 0.0 if rules_on else 50.0, "median": 40000.0}
    return run


def test_the_grid_rebuilds_each_goal_from_the_money_put_in():
    calls = []
    g = fs._goal_grid(_fake_run(calls), target=TARGET, years=YEARS, goal_roi=ROI, monthly=0.0)
    put_in = TARGET / (1 + ROI) ** YEARS
    assert g["put_in_ils"] == pytest.approx(round(put_in), abs=1)
    cells = {(c["roi_pct"], c["extra_years"], c["extra_monthly_ils"]): c for c in g["cells"]}
    assert len(cells) == 20
    # the owner's own goal, unchanged, is the first cell
    assert cells[(30.0, 0.0, 0.0)]["target_ils"] == pytest.approx(round(TARGET), abs=1)
    # a lower ROI is a lower target; a later date at the same ROI is a higher one
    assert cells[(15.0, 0.0, 0.0)]["target_ils"] == pytest.approx(round(put_in * 1.15 ** YEARS), abs=1)
    assert cells[(15.0, 2.0, 0.0)]["target_ils"] > cells[(15.0, 0.0, 0.0)]["target_ils"]
    # each deposit raises the goal by what it grows to at that cell's ROI
    dep = cells[(15.0, 0.0, 1000.0)]["target_ils"] - cells[(15.0, 0.0, 0.0)]["target_ils"]
    assert dep == pytest.approx(fs._fv_monthly(1000.0, 0.15, YEARS), abs=2)


def test_every_cell_is_replayed_with_and_without_the_rules_on_the_same_paths():
    calls = []
    g = fs._goal_grid(_fake_run(calls), target=TARGET, years=YEARS, goal_roi=ROI, monthly=150.0)
    assert len(calls) == 40 and {c["paths"] for c in calls} == {fs.PER_RULE_PATHS}
    assert sum(1 for c in calls if c["rules_on"] is False) == 20
    # standing orders already on the plan stay in every cell
    assert min(c["monthly"] for c in calls) == 150.0
    c = g["cells"][0]
    assert {"p_goal_pct", "p_halve_pct", "p_goal_without_rules_pct", "p_halve_without_rules_pct"} <= set(c)


def test_the_solve_carries_the_grid_on_its_rules_replay():
    """End to end on the PR 99 fixture book: a %/yr goal with rules gets a grid; the
    owner's own goal's cell agrees with the headline (the same replay)."""
    import time

    from tests.test_goal_cards_promise_what_accept_delivers import RULES, _solve
    nav, years, roi = 10_000, 5.0, 0.30
    t0 = time.time()
    v = _solve(nav * (1 + roi) ** years, goal_roi=roi, rules=RULES, years=years)
    took = time.time() - t0
    g = v["rules"]["goal_grid"]
    assert g and len(g["cells"]) == 20
    own = next(c for c in g["cells"] if c["roi_pct"] == 30.0 and not c["extra_years"] and not c["extra_monthly_ils"])
    assert abs(own["p_goal_pct"] - v["rules"]["on"]["p_goal_pct"]) <= 6.0, (own, v["rules"]["on"])
    low = next(c for c in g["cells"] if c["roi_pct"] == 10.0 and not c["extra_years"] and not c["extra_monthly_ils"])
    assert low["p_goal_pct"] >= own["p_goal_pct"], "an easier goal is not less likely"
    print(f"solve with grid: {took:.1f}s")
    # a fixed-amount goal (no ROI) has no grid: no deposit or date changes it
    assert _solve(nav * 2, goal_roi=None, rules=RULES, years=years)["rules"]["goal_grid"] is None


def test_the_grid_stops_at_its_time_budget(monkeypatch):
    calls = []
    monkeypatch.setattr(fs, "GRID_BUDGET_SEC", -1.0)       # already over: nothing computed
    g = fs._goal_grid(_fake_run(calls), target=TARGET, years=YEARS, goal_roi=ROI, monthly=0.0)
    assert calls == [] and g["cells"] == [] and g["not_computed"] == 20
