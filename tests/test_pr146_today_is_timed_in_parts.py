"""PR 146 (speed, measure first): on 2026-10-24 production's warm Today was ~0.5 s of server
time, of which `holding_verdicts` was 268-323 ms in five reads -- but that mark timed
everything from the first query to the verdicts, so the cost could not be attributed.
It is split in four; nothing else changes.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402


def test_today_reports_the_split_stages_in_order():
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "TMV", "market": "NYSE", "asset_class": "Equities", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 10, "cost_basis": 80}]})
        t = c.get("/api/v1/recommendations").json()["timings_ms"]
    keys = [k for k in t if k in ("load", "book_target", "allocation_cards", "goal_projection", "holding_verdicts")]
    assert keys == ["load", "book_target", "allocation_cards", "goal_projection", "holding_verdicts"], t
    assert all(isinstance(t[k], int) and t[k] >= 0 for k in keys)
