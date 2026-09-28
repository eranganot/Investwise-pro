"""PR 74 -- the only things that move the goal: money in or out, or editing
the ROI or the deadline."""
from datetime import date
from types import SimpleNamespace

import pytest

from app.services.goal_service import annual_roi, goal_from

TODAY = date(2026, 9, 28)


def _plan(**kw):
    base = dict(target_roi_pct=30.0, target_roi_period="yearly", target_date="2031",
                horizon_years=6, target_amount=None, created_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_a_deposit_adds_exactly_its_own_compounded_share():
    flows = [(date(2026, 8, 3), 20_000.0)]
    before = goal_from(_plan(), flows, 25_000, today=TODAY)["target"]
    dep = date(2026, 10, 1)
    after = goal_from(_plan(), flows + [(dep, 1_000.0)], 25_000, today=TODAY)["target"]
    t = (date(2031, 12, 31) - dep).days / 365.25
    assert after - before == pytest.approx(1_000 * 1.3 ** t, abs=0.02)


def test_a_withdrawal_takes_its_compounded_share_out():
    flows = [(date(2026, 8, 3), 20_000.0)]
    before = goal_from(_plan(), flows, 25_000, today=TODAY)["target"]
    after = goal_from(_plan(), flows + [(date(2026, 9, 1), -2_000.0)], 25_000, today=TODAY)["target"]
    assert after < before


def test_editing_the_roi_or_the_deadline_moves_it():
    flows = [(date(2026, 8, 3), 20_000.0)]
    base = goal_from(_plan(), flows, 25_000, today=TODAY)["target"]
    assert goal_from(_plan(target_roi_pct=20.0), flows, 25_000, today=TODAY)["target"] < base
    assert goal_from(_plan(target_date="2033"), flows, 25_000, today=TODAY)["target"] > base


def test_a_monthly_roi_compounds_monthly():
    assert annual_roi(_plan(target_roi_pct=2.0, target_roi_period="monthly")) == \
        pytest.approx(1.02 ** 12 - 1)


def test_no_ledger_keeps_the_saved_amount_and_says_why():
    g = goal_from(_plan(target_amount=50_000), [], 25_000, today=TODAY)
    assert g["basis"] == "saved_amount" and g["target"] == 50_000
    assert "No deposits are logged" in g["breakdown"]


def test_without_a_deadline_the_date_is_fixed_by_the_first_deposit():
    g = goal_from(_plan(target_date=None), [(date(2026, 8, 3), 20_000.0)], 25_000, today=TODAY)
    assert g["deadline"] == "2032-08-03" and g["deadline_source"] == "horizon_from_first_deposit"


def test_through_the_api_a_deposit_raises_the_target():
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    from fastapi.testclient import TestClient

    import app.main as m
    with TestClient(m.app) as c:
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20_000, "mode": "set"})
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "DEPX", "market": "NYSE", "asset_class": "Equities", "depth": 3,
             "spot_price": 100, "listing_price": 100, "quantity": 200, "cost_basis": 100}]})
        c.put("/api/v1/plan", json={"objective": "Grow", "risk_tolerance": "High",
                                    "horizon_years": 6, "target_date": "2031",
                                    "target_roi_pct": 30, "target_roi_period": "yearly"})
        t1 = c.get("/api/v1/plan").json()["target_amount"]
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 1_000})
        t2 = c.get("/api/v1/plan").json()["target_amount"]
    assert t2 > t1
    assert t2 - t1 == pytest.approx(t1 / 20.0, rel=0.01), "same date, 1/20 of the money"
