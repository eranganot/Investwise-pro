"""PR 76 -- a compound return is projected as a compound return, once.

Root cause (proven): /plan/projection and the behind-goal card fed a CAGR to
`SimulationEngine` as its arithmetic drift, so the volatility drag was taken
twice. The risk report ("drift_convention": "cagr") and the target solver
(`geometric_to_arithmetic_pct`) already did it right.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.engines.return_convention import drift_pct  # noqa: E402
from app.engines.simulation_engine import SimulationEngine  # noqa: E402

NAV, R, V = 25_148.0, 24.98, 44.62      # Eran's book, 2026-09-28


def _median(ret, vol=V, years=6.0):
    return SimulationEngine(seed=7).run(initial_value=NAV, expected_return_pct=ret,
                                        volatility_pct=vol, horizon_years=years).nominal.p50


def test_erans_book_before_and_after():
    """Reproduces the morning's live figure (₪62,708 on the screen; the exact
    inputs of that request are not available, so within 3%) and the fix."""
    old = _median(R)
    assert old == pytest.approx(62_708, rel=0.03)
    assert round(old) == 61_049
    new = _median(drift_pct(R, V))
    assert round(new) == 94_449
    assert new == pytest.approx(NAV * (1 + R / 100) ** 6, rel=0.02), "median ~ compounding"


@pytest.mark.parametrize("r,v", [(7.5, 15.0), (12.0, 55.0), (24.98, 44.62), (3.5, 6.0)])
def test_the_median_compounds_at_the_stated_rate(r, v):
    assert _median(drift_pct(r, v), v, 5.0) == pytest.approx(NAV * (1 + r / 100) ** 5, rel=0.03)


def test_the_leveraged_table_row_no_longer_loses_money_at_the_median():
    """12%/yr at 55% vol read as an arithmetic drift had a NEGATIVE median --
    the table would have said holding a leveraged fund loses money."""
    assert _median(12.0, 55.0, 5.0) < NAV
    assert _median(drift_pct(12.0, 55.0), 55.0, 5.0) > NAV


def test_one_converter_for_every_consumer():
    from app.services.target_solver import geometric_to_arithmetic_pct
    assert geometric_to_arithmetic_pct(R, V) == drift_pct(R, V)


def test_the_plan_projection_says_its_convention_and_compounds():
    with TestClient(m.app) as c:
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20_000, "mode": "set"})
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "PRJX", "market": "NYSE", "asset_class": "Equities", "depth": 3,
             "spot_price": 100, "listing_price": 100, "quantity": 100, "cost_basis": 100,
             "expected_return_pct": 10.0, "volatility_pct": 30.0}]})
        c.put("/api/v1/plan", json={"objective": "Grow", "risk_tolerance": "High",
                                    "horizon_years": 6, "target_date": "2031",
                                    "target_roi_pct": 30, "target_roi_period": "yearly"})
        pj = c.get("/api/v1/plan/projection").json()
    assert pj["return_convention"] == "compound" and "second time" in pj["return_convention_note"]
    roi = float(pj["assumptions"][0].split("%")[0].lstrip("~"))
    nav = pj["starting_value"]
    assert pj["projected_median"] == pytest.approx(nav * (1 + roi / 100) ** pj["years"], rel=0.04)


def test_the_behind_card_and_the_projection_quote_one_median():
    from app.services.recommendations import _goal_levers
    lv = _goal_levers(nav=NAV, rate=R, vol=V, years=5.26, target=82_692.0,
                      standing_monthly=0.0, cons_rate=8.5, cons_vol=16.0)
    same = SimulationEngine(seed=7).run(initial_value=NAV, expected_return_pct=drift_pct(R, V),
                                        volatility_pct=V, horizon_years=5.26,
                                        target_value=82_692.0).nominal.p50
    assert lv["projected"] == pytest.approx(same)
    assert lv["projected"] == pytest.approx(NAV * (1 + R / 100) ** 5.26, rel=0.03)
    # The conservative line is a compound 8.5% too, so it sits BELOW the headline.
    assert lv["conservative"] < lv["projected"]
