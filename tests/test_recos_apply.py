"""Behind-goal recommendations + accept(apply) mutating holdings/plan."""
import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
from fastapi.testclient import TestClient
import app.main as m
from app.services.plan_service import plan_settings
from types import SimpleNamespace


def test_plan_settings_carries_objective_and_caps():
    plan = SimpleNamespace(risk_tolerance="Low", preferred_depth=2, objective="Income")
    s = plan_settings(plan)
    assert s.objective == "Income"
    assert s.preferred_depth == 2
    assert s.concentration_cap == 0.15  # Low


def test_behind_goal_recs_and_lower_target_apply():
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "TEVA", "market": "NYSE", "asset_class": "Equities", "depth": 3,
             "spot_price": 100, "listing_price": 100, "quantity": 300, "cost_basis": 100,
             "expected_return_pct": 7, "volatility_pct": 14},
            {"ticker": "BOND", "market": "TASE", "asset_class": "Fixed Income", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 200, "cost_basis": 100,
             "expected_return_pct": 3, "volatility_pct": 5}]})
        c.put("/api/v1/plan", json={"objective": "Balanced", "risk_tolerance": "Medium",
                                    "horizon_years": 5, "target_amount": 10_000_000})
        recs = c.get("/api/v1/recommendations").json()["recommendations"]
        goal = [r for r in recs if r["dimension"] == "goal"]
        assert goal, "expected behind-goal recommendations"
        lower = next(r for r in goal if r["apply"]["kind"] == "set_plan"
                     and "target_amount" in r["apply"]["fields"])
        assert c.post(f"/api/v1/recommendations/{lower['id']}/accept").status_code == 200
        plan = c.get("/api/v1/plan").json()
        assert plan["target_amount"] < 10_000_000  # lowered to a realistic level


def test_accept_rebalance_moves_mix_toward_objective():
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "BIGEQ", "market": "NYSE", "asset_class": "Equities", "depth": 3,
             "spot_price": 100, "listing_price": 100, "quantity": 900, "cost_basis": 100},
            {"ticker": "SMBOND", "market": "TASE", "asset_class": "Fixed Income", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 100, "cost_basis": 100}]})
        c.put("/api/v1/plan", json={"objective": "Balanced", "risk_tolerance": "Medium"})
        before = c.get("/api/v1/mix").json()["current_allocation"]["Equities"]
        recs = c.get("/api/v1/recommendations").json()["recommendations"]
        reb = next(r for r in recs if r["apply"]["kind"] == "rebalance_to_objective")
        assert c.post(f"/api/v1/recommendations/{reb['id']}/accept").status_code == 200
        after = c.get("/api/v1/mix").json()["current_allocation"]["Equities"]
        assert after < before  # equities trimmed toward the 60% target
        assert c.post("/api/v1/recommendations/rec_zzzzzz/accept").status_code == 404
