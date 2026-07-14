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
        assert data, "expected per-holding suggestions"
        holding = next(h for h in data if h["ticker"] == "AAA")
        kinds = {r["rule_type"] for r in holding["rules"]}
        assert {"stop_loss", "trailing_stop"} <= kinds
        for r in holding["rules"]:
            assert isinstance(r["level"], (int, float)) and r["ticker"] == "AAA"
            assert r["why"]


def test_accept_trend_card_arms_the_rule_end_to_end(monkeypatch):
    monkeypatch.setattr("app.providers.registry.guarded_history",
                        lambda tk, days=200: _down_closes() if tk == "DOWN" else None)
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "DOWN", "market": "NYSE", "asset_class": "Equities", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 100, "cost_basis": 100},
            {"ticker": "KEEP", "market": "TASE", "asset_class": "Fixed Income", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 100, "cost_basis": 100}]})
        recs = c.get("/api/v1/recommendations").json()["recommendations"]
        card = next(r for r in recs if r["apply"]["kind"] == "create_rules")
        res = c.post(f"/api/v1/recommendations/{card['id']}/accept").json()
        assert res["applied"] == "create_rules"
        assert res["rules_created"] and res["rules_created"][0]["ticker"] == "DOWN"
        rules = c.get("/api/v1/rules").json()["rules"]
        assert any(r["ticker"] == "DOWN" and r["rule_type"] == "stop_loss" for r in rules)
