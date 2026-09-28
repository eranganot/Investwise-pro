"""PR 74 review findings, each pinned: a passed deadline, withdrawals that
cancel the deposits, a re-submitted opening balance, odd deadline formats, an
ROI with no date to grow to."""
import os
from datetime import date
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services.goal_service import goal_from  # noqa: E402

TODAY = date(2026, 9, 28)


def _plan(**kw):
    base = dict(target_roi_pct=30.0, target_roi_period="yearly", target_date="2031",
                horizon_years=6, target_amount=None, created_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_withdrawals_that_cancel_the_deposits_leave_no_goal_not_a_negative_one():
    g = goal_from(_plan(), [(date(2026, 8, 3), 20_000.0), (date(2026, 9, 1), -25_000.0)],
                  10_000, today=TODAY)
    assert g["target"] is None and g["progress"] is None
    assert g["basis"] == "nothing_put_in" and "Withdrawals" in g["breakdown"]


def test_the_deadline_year_governs_whatever_the_format():
    for raw in ("2031", "2031/06/30", "2031 end"):
        assert goal_from(_plan(target_date=raw), [(date(2026, 8, 3), 1.0)], 1,
                         today=TODAY)["deadline"] == "2031-12-31", raw
    assert goal_from(_plan(target_date="2031-06-30"), [(date(2026, 8, 3), 1.0)], 1,
                     today=TODAY)["deadline"] == "2031-06-30"


def test_an_roi_with_no_date_to_grow_to_quotes_no_stale_number():
    g = goal_from(_plan(target_date=None, horizon_years=0, target_amount=125_352),
                  [(date(2026, 8, 3), 20_000.0)], 25_000, today=TODAY)
    assert g["target"] is None and g["basis"] == "no_deadline"


def test_a_leap_day_anchor_lands_on_28_february():
    g = goal_from(_plan(target_date=None, horizon_years=5), [(date(2028, 2, 29), 1_000.0)],
                  1, today=date(2028, 3, 1))
    assert g["deadline"] == "2033-02-28"


def _seed(c, target_date="2031"):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "EDGE", "market": "NYSE", "asset_class": "Equities", "depth": 3,
         "spot_price": 100, "listing_price": 100, "quantity": 50, "cost_basis": 100}]})
    c.put("/api/v1/plan", json={"objective": "Grow", "risk_tolerance": "High",
                                "horizon_years": 6, "target_date": target_date,
                                "target_roi_pct": 30, "target_roi_period": "yearly"})


def test_resubmitting_the_same_opening_balance_does_not_move_the_goal():
    with TestClient(m.app) as c:
        c.post("/api/v1/portfolio/contributions",
               json={"amount_ils": 20_000, "mode": "set", "occurred_at": "2026-08-03T12:00:00Z"})
        _seed(c)
        t1 = c.get("/api/v1/plan").json()["target_amount"]
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20_000, "mode": "set"})
        g2 = c.get("/api/v1/plan").json()
    assert g2["target_amount"] == t1, "same money, same date, same goal"
    assert g2["goal"]["contributions"][0]["at"] == "2026-08-03"


def test_a_passed_deadline_is_said_once_not_projected_over_the_horizon():
    with TestClient(m.app) as c:
        c.post("/api/v1/portfolio/contributions",
               json={"amount_ils": 20_000, "mode": "set", "occurred_at": "2024-01-01T00:00:00Z"})
        _seed(c, target_date="2025")
        plan = c.get("/api/v1/plan").json()
        proj = c.get("/api/v1/plan/projection").json()
        recs = c.get("/api/v1/recommendations").json()
    assert plan["goal"]["years_left"] == 0
    assert "has passed" in proj["message"] and proj["years"] == 0
    goal_cards = [r for r in recs["recommendations"] + recs.get("more_recommendations", [])
                  if r.get("dimension") == "goal"]
    assert goal_cards == []
