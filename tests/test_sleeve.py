"""A rule-based strategy is a sleeve, not a portfolio.

Before this, applying "trend-filtered 3x Nasdaq" put the ENTIRE book into TQQQ,
because the model basket says 100% TQQQ when read in isolation. There was no way
to express "a fifth of my money follows this rule" -- which is the only way
anyone sane runs a leveraged strategy.
"""
import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi.testclient import TestClient

import app.main as m
from app.services import strategy_catalog as sc


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "V", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
         "spot_price": 365, "listing_price": 365, "quantity": 10, "cost_basis": 300,
         "expected_return_pct": 8, "volatility_pct": 20}]})


def test_a_sleeve_splits_the_basket_between_the_rule_and_the_core():
    legs = dict(sc.sleeve_basket("btm_trend_tqqq", 20))
    assert legs["TQQQ"] == 0.20, "a fifth of the book follows the rule"
    assert legs["QQQ"] == 0.80, "the rest stays in the core"
    assert abs(sum(legs.values()) - 1.0) < 1e-6


def test_zero_and_full_are_both_expressible():
    assert dict(sc.sleeve_basket("btm_trend_tqqq", 0)) == {"QQQ": 1.0}
    assert dict(sc.sleeve_basket("btm_trend_tqqq", 100)) == {"TQQQ": 1.0}


def test_omitting_the_sleeve_falls_back_to_the_suggestion_not_to_everything():
    """The dangerous default. 100% of a book in a 3x fund is not a sane
    fallback for 'the user did not say'."""
    legs = dict(sc.sleeve_basket("btm_trend_tqqq"))
    suggested = sc.get("btm_trend_tqqq")["sleeve_pct"] / 100.0
    assert legs["TQQQ"] == suggested < 1.0


def test_a_strategy_with_no_core_is_unaffected():
    """The unleveraged families have nothing to fall back to -- the whole
    allocation IS the strategy, so a sleeve would be meaningless."""
    legs = dict(sc.sleeve_basket("btm_factor_stack", 50))
    assert abs(sum(legs.values()) - 1.0) < 1e-6
    assert set(legs) == {"MTUM", "QUAL", "AVUV"}


def test_applying_remembers_the_sleeve():
    """Applying at 20% and reloading at the default would quietly quadruple the
    exposure the user chose."""
    with TestClient(m.app) as c:
        _seed(c)
        r = c.post("/api/v1/strategies/btm_trend_tqqq/apply?sleeve_pct=15")
        assert r.status_code == 200
        plan = c.get("/api/v1/plan").json()
        assert plan["strategy"] == "btm_trend_tqqq"
        assert plan["strategy_sleeve_pct"] == 15.0


def test_the_card_carries_both_the_current_and_the_suggested_sleeve():
    with TestClient(m.app) as c:
        _seed(c)
        cards = c.get("/api/v1/strategies").json()["by_goal"][sc.GOAL]
        for card in cards:
            assert card["sleeve_pct"] is not None
            assert card["sleeve_default_pct"] is not None


def test_preview_reflects_the_sleeve_it_was_asked_about():
    with TestClient(m.app) as c:
        _seed(c)
        small = c.get("/api/v1/strategies/btm_trend_tqqq/preview?sleeve_pct=10").json()
        big = c.get("/api/v1/strategies/btm_trend_tqqq/preview?sleeve_pct=90").json()
        assert small["ok"] and big["ok"]
        s_legs = dict(small["strategy"]["basket"])
        b_legs = dict(big["strategy"]["basket"])
        assert s_legs["TQQQ"] == 0.10 and b_legs["TQQQ"] == 0.90
