"""Accept must not pretend to act.

Reported from production: tapping Accept on "You're trailing SPY" changed
nothing, said "Done -- applied.", and moved the card into the ignored list. Cause:
advisory cards carry apply.kind == "none", fall through every branch of
apply_recommendation, and the route dismisses them regardless.
"""
import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
from fastapi.testclient import TestClient
import app.main as m
from app.services.recommendations import _ACTIONABLE_KINDS, _is_actionable


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "AAA", "market": "TASE", "asset_class": "Equities", "depth": 3,
         "spot_price": 100, "listing_price": 100, "quantity": 400, "cost_basis": 100,
         "expected_return_pct": 7, "volatility_pct": 14},
        {"ticker": "BBB", "market": "TASE", "asset_class": "Equities", "depth": 2,
         "spot_price": 90, "listing_price": 90, "quantity": 10, "cost_basis": 100,
         "expected_return_pct": 5, "volatility_pct": 10},
    ]})


def test_is_actionable_classifies_kinds():
    assert _is_actionable({"apply": {"kind": "trim"}}) is True
    assert _is_actionable({"apply": {"kind": "none"}}) is False
    assert _is_actionable({}) is False              # missing apply == advisory
    assert "trim" in _ACTIONABLE_KINDS and "none" not in _ACTIONABLE_KINDS


def test_every_card_declares_whether_the_app_can_execute_it():
    with TestClient(m.app) as c:
        _seed(c)
        recs = c.get("/api/v1/recommendations").json()["recommendations"]
        assert recs, "expected at least one recommendation"
        for r in recs:
            assert "actionable" in r, f"{r['title']} doesn't declare actionable"
            assert isinstance(r["actionable"], bool)
            assert r["actionable"] == _is_actionable(r)


def test_advisory_accept_reports_that_nothing_was_traded():
    with TestClient(m.app) as c:
        _seed(c)
        recs = c.get("/api/v1/recommendations").json()["recommendations"]
        advisory = next((r for r in recs if not r["actionable"]), None)
        if advisory is None:
            return
        before = c.get("/api/v1/portfolio").json()
        j = c.post(f"/api/v1/recommendations/{advisory['id']}/accept").json()
        assert j["advisory"] is True
        assert j["applied"] == "none"
        assert "nothing was bought or sold" in j["note"]
        after = c.get("/api/v1/portfolio").json()
        # the honest part: an advisory accept really does leave the book alone
        assert after["nav_ils"] == before["nav_ils"]
        assert after["count"] == before["count"]


def test_actionable_accept_still_mutates_the_portfolio():
    with TestClient(m.app) as c:
        _seed(c)
        recs = c.get("/api/v1/recommendations").json()["recommendations"]
        act = next((r for r in recs if r["actionable"]
                    and r["apply"]["kind"] in ("trim", "sell_losers")), None)
        if act is None:
            return
        before = c.get("/api/v1/portfolio").json()
        j = c.post(f"/api/v1/recommendations/{act['id']}/accept").json()
        assert j.get("advisory") is not True
        after = c.get("/api/v1/portfolio").json()
        assert after["cash_ils"] > before["cash_ils"]     # proceeds are visible


def test_unknown_recommendation_returns_404_so_the_ui_can_drop_it():
    with TestClient(m.app) as c:
        _seed(c)
        r = c.post("/api/v1/recommendations/rec_deadbe/accept")
        assert r.status_code == 404
        # and it must NOT have been recorded as a dismissal
        assert c.get("/api/v1/recommendations").json()["dismissed_count"] == 0
