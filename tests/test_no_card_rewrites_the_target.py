"""PR 74 -- no card moves the goalposts.

"Set a target you'll actually hit" rewrote the target to the projection and
"Give it more time" rewrote the horizon. Eran removed both (Q2, 2026-09-28).
"""
import inspect
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services import recommendations as recs_mod  # noqa: E402

_GOAL_FIELDS = {"target_amount", "horizon_years", "target_date", "target_roi_pct"}


def test_the_two_cards_are_gone_from_the_source():
    # The docstring names them on purpose (why they went); the code must not.
    src = inspect.getsource(recs_mod._behind_goal_recs).replace(
        recs_mod._behind_goal_recs.__doc__, "")
    assert "actually hit" not in src and "Give it more time" not in src
    assert '"set_plan"' not in src


def test_no_card_on_today_can_edit_the_goal():
    with TestClient(m.app) as c:
        c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20_000, "mode": "set"})
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "NOREW", "market": "NYSE", "asset_class": "Equities", "depth": 3,
             "spot_price": 100, "listing_price": 100, "quantity": 30, "cost_basis": 100}]})
        c.put("/api/v1/plan", json={"objective": "Balanced", "risk_tolerance": "Medium",
                                    "horizon_years": 6, "target_date": "2031",
                                    "target_roi_pct": 30, "target_roi_period": "yearly"})
        out = c.get("/api/v1/recommendations").json()
    every = out["recommendations"] + out.get("more_recommendations", [])
    assert any(r.get("dimension") == "goal" for r in every), "the book is behind"
    for r in every:
        ap = r.get("apply") or {}
        if ap.get("kind") == "set_plan":
            assert not (_GOAL_FIELDS & set((ap.get("fields") or {}))), r["title"]
