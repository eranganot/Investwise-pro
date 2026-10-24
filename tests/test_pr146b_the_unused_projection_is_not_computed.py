"""PR 146b (measured 2026-10-24). Production's Today spent a median 250 ms in `goal_projection`;
`_goal_levers` alone measured 220-325 ms on the same inputs (a Monte-Carlo bisection). Its only
reader is the behind-your-goal card, which stands down whenever a fresh goal verdict owns the
goal (PR 92) -- true on production -- so the 250 ms bought nothing. It is now skipped then, and
still computed (and the card still built) when the older card speaks.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services import recommendations as rr  # noqa: E402


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "GLV", "market": "NYSE", "asset_class": "Equities", "depth": 2,
         "spot_price": 100, "listing_price": 100, "quantity": 100, "cost_basis": 80}]})
    c.put("/api/v1/plan", json={"objective": "Grow", "target_roi_pct": 30, "target_date": "2031"})
    c.post("/api/v1/portfolio/contributions", json={"amount_ils": 8000, "mode": "set"})


@pytest.mark.parametrize("owned", [True, False])
async def test_the_projection_runs_only_for_the_card_that_reads_it(monkeypatch, owned, own_session):
    from app.services import frontier_service as fs
    calls = []
    real = rr._goal_levers

    def spy(**kw):
        calls.append(kw)
        return real(**kw)

    async def story(session, user):
        return owned

    monkeypatch.setattr(rr, "_goal_levers", spy)
    monkeypatch.setattr(fs, "goal_story_active", story)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            j = c.get("/api/v1/recommendations").json()
    finally:
        from tests.test_frontier_goal import _reset_plan
        await _reset_plan(own_session)
    assert j.get("timings_ms", {}).get("goal_projection") is not None
    assert (len(calls) == 0) if owned else (len(calls) == 1), (owned, len(calls))
