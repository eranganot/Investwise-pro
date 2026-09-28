"""PR 74 -- one target, one deadline, one return, everywhere.

Measured before the fix (production, 2026-09-28): /plan said ₪121,386 over 6
years at the measured 24.98%; the "You're behind" card said ₪113,039 over 5
years at the static 8.5%; /plan/projection said ₪121,386 over 6.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402


def _setup(c):
    c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20_000, "mode": "set"})
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "ONEG", "market": "NYSE", "asset_class": "Equities", "depth": 3,
         "spot_price": 100, "listing_price": 100, "quantity": 50, "cost_basis": 100,
         "expected_return_pct": 7, "volatility_pct": 14}]})
    c.put("/api/v1/plan", json={"objective": "Grow", "risk_tolerance": "High",
                                "horizon_years": 6, "target_date": "2031",
                                "target_roi_pct": 30, "target_roi_period": "yearly"})


def test_plan_projection_and_the_behind_card_quote_one_goal():
    with TestClient(m.app) as c:
        _setup(c)
        plan = c.get("/api/v1/plan").json()
        proj = c.get("/api/v1/plan/projection").json()
        recs = c.get("/api/v1/recommendations").json()
    cards = [r for r in recs["recommendations"] + recs.get("more_recommendations", [])
             if r.get("dimension") == "goal" and r["apply"]["kind"] == "set_standing_order"]
    assert cards, "a ₪5,000 book against a ₪20,000-at-30% goal is behind"
    card = cards[0]
    g = plan["goal"]
    assert g["basis"] == "contributions" and g["deadline"] == "2031-12-31"
    # one target
    assert plan["target_amount"] == proj["target_amount"] == card["goal_target_ils"] == g["target"]
    # one deadline, one number of years
    assert proj["deadline"] == card["goal_deadline"] == g["deadline"]
    assert proj["years"] == card["goal_years_left"] == round(g["years_left"], 1)
    # one return: the card's headline is /plan's, the table rate is the second line
    assert card["return_pct"] == pytest.approx(plan["portfolio_expected_roi_pct"], abs=0.01)
    assert card["return_basis"] == plan["assumption_basis"]
    assert card["conservative_return_pct"] == 8.5
    if plan["assumption_basis"] in (None, "static"):
        # the headline already IS a table rate; a second one is noise
        assert "conservative" not in card["why"]


def test_the_projection_years_count_to_the_deadline_not_the_horizon():
    with TestClient(m.app) as c:
        _setup(c)
        proj = c.get("/api/v1/plan/projection").json()
    assert proj["years"] < 6, "horizon 6, but the deadline (31 Dec 2031) governs (Q1)"


def _card(**kw):
    from app.services.recommendations import _behind_goal_recs
    from types import SimpleNamespace
    goal = {"target": 82_692.0, "years_left": 5.257, "deadline": "2031-12-31",
            "breakdown": "₪20,000 put in ...", "basis": "contributions", "put_in": 20_000}
    args = dict(plan=SimpleNamespace(), snap={"nav": 25_148.0}, objective="Grow",
                projected_override=None, standing_monthly=0.0, goal=goal,
                expected_roi_pct=24.98, return_basis="measured")
    args.update(kw)
    out = _behind_goal_recs(args.pop("plan"), args.pop("snap"), args.pop("objective"),
                            args.pop("projected_override"), **args)
    return out[0] if out else None


def test_measured_headline_with_the_conservative_line_under_it():
    # 25,148 at 24.98% for 5.26y ~ 81k < 82.7k: behind, just.
    c = _card()
    assert c is not None
    assert c["return_pct"] == 24.98 and "measured ~25.0%/yr" in c["why"]
    assert "At a conservative 8.5%/yr" in c["why"]
    assert c["conservative_projection_ils"] < 25_148 * 1.2498 ** 5.257
    assert c["goal_years_left"] == 5.3 and "2031-12-31" in c["action"]


def test_the_amount_on_the_card_really_closes_the_gap_on_the_same_engine():
    """Review, CONFIRMED: sized with plain compounding beside a Monte-Carlo
    headline, "about ₪680/month closes the gap" left the median at ₪88,299
    against ₪120,000. The amount is now searched on the engine itself."""
    from app.engines.simulation_engine import SimulationEngine
    from app.services.recommendations import _goal_levers
    kw = dict(nav=30_000.0, rate=25.0, vol=67.5, years=5.26, target=120_000.0,
              standing_monthly=0.0, cons_rate=8.5, cons_vol=20.0)
    lv = _goal_levers(**kw)

    from app.engines.return_convention import drift_pct

    def median(monthly):
        # PR 76: 25% is a COMPOUND rate; the engine gets its drift.
        return SimulationEngine(seed=7).run(
            initial_value=30_000, expected_return_pct=drift_pct(25, 67.5), volatility_pct=67.5,
            horizon_years=5.26, target_value=120_000,
            contribution_per_year=monthly * 12).nominal.p50
    assert lv["projected"] < 120_000
    assert median(lv["pmt"]) >= 120_000, "the stated amount reaches the goal"
    assert median(lv["pmt"] - 10) < 120_000, "and it is the smallest whole ₪10 that does"
    c = _card(snap={"nav": 30_000.0}, goal={"target": 120_000.0, "years_left": 5.26,
              "deadline": "2031-12-31", "basis": "contributions"},
              expected_roi_pct=25.0, levers=lv, volatility_pct=67.5)
    assert c["est_amount"] == lv["pmt"]
    assert "median of 10,000 simulations at your measured ~25.0%/yr and ~68% volatility" in c["why"]
    # with ~68% vol the 25% median sits BELOW the 8.5%-at-20% one: not "conservative"
    word = "a conservative" if lv["conservative"] < lv["projected"] else "the objective table's"
    assert f"At {word} 8.5%/yr" in c["why"]


def test_lever_b_compares_like_with_like():
    c = _card(objective="Balanced", return_basis="static", expected_roi_pct=6.5)
    from app.services.recommendations import _behind_goal_recs
    from types import SimpleNamespace
    out = _behind_goal_recs(SimpleNamespace(), {"nav": 25_148.0}, "Balanced", None,
                            goal={"target": 82_692.0, "years_left": 5.257, "deadline": "2031-12-31"},
                            expected_roi_pct=6.5, return_basis="static")
    grow = next(r for r in out if r["apply"]["kind"] == "set_objective_and_rebalance")
    want = 25_148.0 * (1.085 ** 5.257 - 1.065 ** 5.257)
    assert grow["est_amount"] == pytest.approx(want, abs=1.0)
    assert c is not None


def test_a_part_measured_book_is_not_called_measured():
    c = _card(return_basis="mixed", measured_pct=85.9)
    assert "part-measured" in c["why"] and "85.9% of the book measured" in c["why"]


def test_an_unmeasured_book_gets_one_line_not_two():
    c = _card(return_basis="static", expected_roi_pct=8.57)
    assert c is not None and "conservative" not in c["why"]
    assert "instrument table, not measured" in c["why"]
