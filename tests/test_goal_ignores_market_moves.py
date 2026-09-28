"""PR 74 -- the goal is the money you put in, so the MARKET cannot move it.

Before: `_auto_target` = live NAV x (1+roi)^n, so progress was
NAV / (NAV x 1.3^6) = 0.2072 on production -- a constant no gain or loss
could ever change (Eran's item 4, 2026-09-28).
"""
from datetime import date
from types import SimpleNamespace

import pytest

from app.services.goal_service import goal_from

PLAN = SimpleNamespace(target_roi_pct=30.0, target_roi_period="yearly", target_date="2031",
                       horizon_years=6, target_amount=None, created_at=None)
FLOWS = [(date(2026, 8, 3), 20_000.0)]
TODAY = date(2026, 9, 28)


def test_erans_goal_is_his_20000_compounded_to_the_deadline():
    g = goal_from(PLAN, FLOWS, 25_148.0, today=TODAY)
    assert g["basis"] == "contributions"
    assert g["deadline"] == "2031-12-31", "the deadline governs (Q1)"
    years = (date(2031, 12, 31) - date(2026, 8, 3)).days / 365.25
    assert g["target"] == pytest.approx(20_000 * 1.3 ** years, abs=0.01)
    assert 82_000 < g["target"] < 83_500, "the plan measured ~82,700"
    assert g["put_in"] == 20_000
    assert "20,000 put in" in g["breakdown"]


@pytest.mark.parametrize("move", [-0.30, -0.05, 0.0, 0.05, 0.30])
def test_a_market_move_changes_progress_and_not_the_target(move):
    base = goal_from(PLAN, FLOWS, 25_148.0, today=TODAY)
    moved = goal_from(PLAN, FLOWS, 25_148.0 * (1 + move), today=TODAY)
    assert moved["target"] == base["target"]
    assert moved["on_track_value"] == base["on_track_value"]
    if move:
        assert moved["progress"] != base["progress"]
        assert (moved["ahead_by"] > base["ahead_by"]) == (move > 0)


def test_progress_is_not_the_old_constant():
    g = goal_from(PLAN, FLOWS, 25_148.0, today=TODAY)
    assert g["progress"] != pytest.approx(1 / 1.3 ** 6, abs=1e-4)
    assert g["progress"] == pytest.approx(25_148.0 / g["target"], abs=1e-4)


def test_the_target_does_not_drift_with_the_calendar():
    a = goal_from(PLAN, FLOWS, 25_148.0, today=date(2026, 9, 28))
    b = goal_from(PLAN, FLOWS, 25_148.0, today=date(2027, 3, 1))
    assert a["target"] == b["target"]
    assert b["on_track_value"] > a["on_track_value"], "where you should be keeps compounding"
    assert b["years_left"] < a["years_left"]


def test_through_the_api_a_price_move_leaves_the_target_alone():
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    from fastapi.testclient import TestClient

    import app.main as m

    def book(price):
        return {"entity_name": "Personal", "positions": [
            {"ticker": "GOALX", "market": "NYSE", "asset_class": "Equities", "depth": 3,
             "spot_price": price, "listing_price": price, "quantity": 100, "cost_basis": 100}]}

    with TestClient(m.app) as c:
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20_000, "mode": "set"})
        c.post("/api/v1/intake/portfolio", json=book(100))
        c.put("/api/v1/plan", json={"objective": "Grow", "risk_tolerance": "High",
                                    "horizon_years": 6, "target_date": "2031",
                                    "target_roi_pct": 30, "target_roi_period": "yearly"})
        p1 = c.get("/api/v1/plan").json()
        c.post("/api/v1/intake/portfolio", json=book(130))
        p2 = c.get("/api/v1/plan").json()
    assert p1["goal"]["basis"] == "contributions"
    assert p1["target_amount"] == p2["target_amount"]
    assert p2["current_value"] > p1["current_value"]
    assert p2["goal_progress"] > p1["goal_progress"]
