"""PR 104d: the card thresholds are settings (Eran, 2026-10-07: "I want every
decision to be controlled and changeable through the app").

Defaults stay where they lived; `plans.thresholds` holds overrides; the request
/ job context carries them to every reader.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import thresholds


def test_the_defaults_are_the_constants_that_own_them():
    from app.services import frontier_service as fs
    from app.services import funding_service as fund
    from app.services import headsup
    d = thresholds.defaults()
    assert d["min_card_tax_ils"] == headsup.MIN_TAX_SAVING_ILS == 50.0
    assert d["min_card_trade_ils"] == 100.0 and d["min_card_trade_pct"] == 0.5
    assert d["min_funded_trade_ils"] == fund.MIN_TRADE_DEFAULT_ILS == 250.0
    assert d["cash_floor_ils"] == 150.0 and d["rebalance_band_pts"] == 3.0
    assert d["take_profit_fraction_pct"] == 25.0 and d["gain_line_pct"] == 25.0
    assert d["material_odds_pts"] == fs.MATERIAL_PROB_PTS and d["guard_levels"] == [15.0, 17.5, 20.0, 25.0]  # PR 121 widened
    assert set(d) == set(thresholds.SPEC)


def test_clean_keeps_known_keys_inside_their_bounds():
    assert thresholds.clean({"cash_floor_ils": 300, "guard_levels": [20, 12, 20]}) == \
        {"cash_floor_ils": 300.0, "guard_levels": [12.0, 20.0]}
    for bad in ({"nope": 1}, {"rebalance_band_pts": 0}, {"guard_levels": []}, {"take_profit_fraction_pct": 200}):
        with pytest.raises(ValueError):
            thresholds.clean(bad)


def test_an_override_reaches_every_reader():
    from app.services import advanced_rules, funding_service, headsup, rules_service
    from app.services import frontier_service as fs
    plan = SimpleNamespace(thresholds={"min_funded_trade_ils": 400, "cash_floor_ils": 500, "min_card_trade_ils": 300,
                                       "min_card_trade_pct": 0, "take_profit_fraction_pct": 50,
                                       "rebalance_band_pts": 5, "material_odds_pts": 2, "gain_line_pct": 40})
    token = thresholds._OVERRIDES.set(thresholds.overrides_of(plan))
    try:
        assert funding_service.MIN_TRADE_ILS == 400.0 and funding_service.CASH_HARD_FLOOR_ILS == 500.0
        assert headsup.min_trade_ils(10_000) == 300.0
        assert rules_service.TAKE_PROFIT_FRACTION == 0.5
        assert advanced_rules._band_default() == 5.0
        assert fs._material() == 2.0
        assert thresholds.get("gain_line_pct", 25.0) == 40.0
    finally:
        thresholds._OVERRIDES.reset(token)
    # nothing set: exactly as before
    assert funding_service.MIN_TRADE_ILS == 250.0 and rules_service.TAKE_PROFIT_FRACTION == 0.25


def test_saving_thresholds_is_a_partial_update_and_null_resets():
    with TestClient(app) as c:
        try:
            r = c.put("/api/v1/plan", json={"thresholds": {"cash_floor_ils": 300, "min_card_tax_ils": 20}})
            assert r.status_code == 200
            p = c.get("/api/v1/plan").json()
            assert p["thresholds_overrides"] == {"cash_floor_ils": 300.0, "min_card_tax_ils": 20.0}
            assert p["thresholds"]["cash_floor_ils"] == 300.0 and p["thresholds"]["rebalance_band_pts"] == 3.0
            assert p["thresholds_spec"]["cash_floor_ils"]["label"]
            c.put("/api/v1/plan", json={"thresholds": {"min_card_tax_ils": None}})
            assert c.get("/api/v1/plan").json()["thresholds_overrides"] == {"cash_floor_ils": 300.0}
            assert c.put("/api/v1/plan", json={"thresholds": {"rebalance_band_pts": 99}}).status_code == 422
            assert c.put("/api/v1/plan", json={"thresholds": {"made_up": 1}}).status_code == 422
        finally:
            c.put("/api/v1/plan", json={"thresholds": {k: None for k in thresholds.SPEC}})
        assert c.get("/api/v1/plan").json()["thresholds_overrides"] == {}
