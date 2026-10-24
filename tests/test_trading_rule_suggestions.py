"""Concrete, actionable trend cards + per-holding suggested rule sets.

Covers the two gaps: (1) Accept on a trend card arms a concrete trading rule
(the 'to-do'), and (2) each holding gets a suggested rule set with real levels.
"""
import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
from fastapi.testclient import TestClient

import app.main as m
from app.services import recommendations as R


def _down_closes():
    # steady decline -> short MA below long MA, ~-37% over 120d -> downtrend fires
    return [(i, 200.0 - i * 0.5) for i in range(200)]


def _up_closes():
    # steady climb -> short MA above long MA, ~+42% over 120d -> uptrend fires
    return [(i, 100.0 + i * 0.5) for i in range(200)]


def test_stop_buffer_pct_is_bounded_and_grounded():
    assert R.stop_buffer_pct([], lo=8.0, hi=15.0) == 8.0            # no history -> low bound
    flat = [100.0] * 40
    assert R.stop_buffer_pct(flat, lo=8.0, hi=15.0) == 8.0          # ~zero vol -> low bound
    buf = R.stop_buffer_pct([100.0 + (i % 5) * 3 for i in range(40)], lo=8.0, hi=15.0)
    assert 8.0 <= buf <= 15.0                                       # always clamped


def test_downtrend_card_arms_a_concrete_stop(monkeypatch):
    monkeypatch.setattr("app.providers.registry.guarded_history",
                        lambda tk, days=200: _down_closes() if tk == "DOWN" else None)
    rows = [SimpleNamespace(ticker="DOWN")]
    recs = R._momentum_recs(rows, {"exposure_ticker": {"DOWN": 0.05}})
    assert len(recs) == 1
    card = recs[0]
    assert card["apply"]["kind"] == "create_rules"
    rule = card["apply"]["rules"][0]
    assert rule["rule_type"] == "stop_loss" and rule["mode"] == "price"
    assert 0 < rule["level"] < 200                                  # a real price below today
    assert card["why"] and card["impact"]                          # explained, not bare
    assert any("stop-loss" in step.lower() for step in card["how"])


def test_uptrend_card_arms_trailing_and_caps_big_positions(monkeypatch):
    monkeypatch.setattr("app.providers.registry.guarded_history",
                        lambda tk, days=200: _up_closes() if tk == "UP" else None)
    rows = [SimpleNamespace(ticker="UP")]
    # small position -> just a trailing stop
    small = R._momentum_recs(rows, {"exposure_ticker": {"UP": 0.05}})[0]
    kinds = {r["rule_type"] for r in small["apply"]["rules"]}
    assert kinds == {"trailing_stop"}
    # large position -> trailing stop PLUS a max-weight cap
    big = R._momentum_recs(rows, {"exposure_ticker": {"UP": 0.30}})[0]
    kinds = {r["rule_type"] for r in big["apply"]["rules"]}
    assert kinds == {"trailing_stop", "max_weight"}


def test_suggestions_endpoint_gives_a_rule_set_per_holding():
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "AAA", "market": "NYSE", "asset_class": "Equities", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 100, "cost_basis": 80}]})
        data = c.get("/api/v1/rules/suggestions").json()["suggestions"]
        # A3r (D132): exits are the protection manager's; suggestions carry none.
        kinds = {r["rule_type"] for h in data for r in h["rules"]}
        assert not kinds & {"stop_loss", "trailing_stop", "take_profit", "profit_protect"}, kinds
        for r in [r for h in data for r in h["rules"]]:
            assert isinstance(r["level"], (int, float)) and r["ticker"] == "AAA"
            assert r["why"]


def test_a_paused_rule_is_a_decision_not_a_gap():
    """PR 104a.2: accepting "Retune your protection" paused the sleeve stops, and
    Today answered with "protective rules ready to arm" -- the same stops. A
    paused rule is never suggested back."""
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "PZD", "market": "NYSE", "asset_class": "Equities", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 100, "cost_basis": 80}]})
        r = c.post("/api/v1/rules", json={"ticker": "PZD", "rule_type": "stop_loss", "mode": "price",
                                          "level": 85.0}).json()
        rid = (r.get("rule") or r).get("id")
        assert c.post(f"/api/v1/rules/{rid}/toggle").json()["ok"]          # paused
        data = c.get("/api/v1/rules/suggestions").json()["suggestions"]
        kinds = {x["rule_type"] for h in data if h["ticker"] == "PZD" for x in h["rules"]}
        assert "stop_loss" not in kinds, "a paused stop was offered back as 'ready to arm'"


def test_a_trend_card_no_longer_arms_a_stop(monkeypatch):
    """A3r (D132): the downtrend card armed a stop-loss; exits are the protection manager's now."""
    monkeypatch.setattr("app.providers.registry.guarded_history",
                        lambda tk, days=200: _down_closes() if tk == "DOWN" else None)
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "DOWN", "market": "NYSE", "asset_class": "Equities", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 100, "cost_basis": 100},
            {"ticker": "KEEP", "market": "TASE", "asset_class": "Fixed Income", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 100, "cost_basis": 100}]})
        j = c.get("/api/v1/recommendations").json()
        recs = j["recommendations"] + j.get("more_recommendations", [])
        arming = [r["id"] for r in recs if (r.get("apply") or {}).get("kind") == "create_rules"
                  and any(x.get("rule_type") in ("stop_loss", "trailing_stop", "take_profit", "profit_protect")
                          for x in r["apply"].get("rules") or [])]
        assert arming == []