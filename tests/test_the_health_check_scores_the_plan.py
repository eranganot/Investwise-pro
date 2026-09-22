"""PR 70 -- the health card, the recorded history and the AI summary quote ONE score.

The score moved from the legacy Risk/Tax/Spread/Cash blend to On plan + On track
(`plan_health`). Three readers used `health_scores()` directly; if any of them
had been left behind, the push digest or the AI summary would quote a number
the screen no longer shows -- the two-true-numbers failure of PR 35. These run
the real endpoint and the real snapshot job on a seeded book.

Not financial advice.
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

import app.main as m


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "SOXL", "market": "NYSE", "asset_class": "Equities", "depth": 3,
         "spot_price": 140.0, "listing_price": 140.0, "quantity": 20, "cost_basis": 100.0,
         "expected_return_pct": 8, "volatility_pct": 60},
        {"ticker": "BND", "market": "NYSE", "asset_class": "Fixed Income", "depth": 3,
         "spot_price": 71.0, "listing_price": 71.0, "quantity": 10, "cost_basis": 70.0,
         "expected_return_pct": 4, "volatility_pct": 5}]})


def test_the_health_check_returns_on_plan_and_on_track():
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            h = c.get("/api/v1/health-check").json()
    finally:
        logging.disable(logging.NOTSET)
    assert h["score_basis"] == "on_plan_on_track", h
    assert set(h["plan_parts"]) == {"sleeves", "mix", "rules", "concentration", "cash"}
    halves = [h["on_plan"]] + ([h["on_track"]] if h["on_track"] is not None else [])
    # Computed from the unrounded halves, so it may sit one point off their mean.
    assert abs(h["wealth_health_score"] - sum(halves) / len(halves)) <= 1
    for part in h["plan_parts"].values():
        assert 0 <= part["score"] <= 100 and part["detail"]
    # The legacy blend is still there, labelled, for the readers that use it.
    assert "legacy_score" in h and "risk_score" in h


@pytest.mark.asyncio
async def test_the_recorded_score_is_the_screens_score(own_session):
    from sqlalchemy import select

    from app.models.tables import User, WhsSnapshot
    from app.services.nav_history import record_health_for

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            shown = c.get("/api/v1/health-check").json()["wealth_health_score"]
            async with own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                out = await record_health_for(s, user)
                await s.commit()
                row = (await s.execute(select(WhsSnapshot).order_by(
                    WhsSnapshot.taken_at.desc()))).scalars().first()
    finally:
        logging.disable(logging.NOTSET)
    assert out["ok"], out
    assert out["score"] == shown
    assert round(row.score) == shown and row.detail["score_basis"] == "on_plan_on_track"
