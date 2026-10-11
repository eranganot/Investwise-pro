"""PR 115 (D-c, D-d): the rest of the hard-coded decisions are settings, and
there is ONE minimum trade.

D-d (owner, 2026-10-11): "all of them become editable" -- 13 more keys in
`thresholds.SPEC`, each defaulting to the constant beside the code that uses it.
D-c: the card floor is the largest of the card's ₪ / %-of-book minimums AND the
smallest funded trade. Below a ~₪50k book the ₪100 card minimum was inert
(funding refuses any leg under ₪250 first) and the commodity card degraded into
an un-actable "Buy ₪180 of X" heads-up.
"""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.services import thresholds

NEW = {"concentration_cap_pct", "sleeve_drift_band_pts", "min_resize_pts", "material_gap_close_pct",
       "material_vol_drop_pts", "ignore_mute_days", "done_mute_days", "refire_change_pct", "near_rule_pct",
       "near_trail_giveback_pct", "near_cap_pts", "ideas_shown", "max_idea_cards"}


def _with(overrides):
    return thresholds._OVERRIDES.set(thresholds.clean(overrides))


def test_the_thirteen_are_settings_with_todays_defaults():
    from app.services import frontier_service as fs
    from app.services import recommendations as rec
    assert NEW <= set(thresholds.SPEC) and len(thresholds.SPEC) == 23
    d = thresholds.defaults()
    assert d["concentration_cap_pct"] == 25.0                              # Medium risk tolerance
    assert thresholds.defaults(SimpleNamespace(risk_tolerance="High"))["concentration_cap_pct"] == 40.0
    assert d["sleeve_drift_band_pts"] == rec.SLEEVE_DRIFT_BAND_PCT == 5.0
    assert d["ignore_mute_days"] == 7 and d["done_mute_days"] == 14 and d["refire_change_pct"] == 25.0
    assert d["near_rule_pct"] == 10.0 and d["near_trail_giveback_pct"] == 60.0 and d["near_cap_pts"] == 2.0
    assert d["ideas_shown"] == 5 and d["max_idea_cards"] == 2
    assert d["min_resize_pts"] == fs.MIN_MOVE_PCT == 2.0
    assert d["material_gap_close_pct"] == 33.33 and d["material_vol_drop_pts"] == 3.0
    assert thresholds.clean({"ideas_shown": 7.6}) == {"ideas_shown": 8}


def test_each_override_reaches_its_reader():
    from app.services import frontier_service as fs
    from app.services import recommendations as rec
    from app.services.plan_service import effective_caps
    plan = SimpleNamespace(risk_tolerance="Medium", thresholds={"concentration_cap_pct": 12})
    assert effective_caps(plan)["concentration_cap"] == 0.12                # D-d: the cap is yours
    assert effective_caps(SimpleNamespace(risk_tolerance="Medium", thresholds=None))["concentration_cap"] == 0.25
    tok = _with({"min_resize_pts": 7, "material_gap_close_pct": 50, "material_vol_drop_pts": 9,
                 "sleeve_drift_band_pts": 1.5, "ignore_mute_days": 3})
    try:
        assert fs._min_move() == 7.0 and fs._material_gap() == 0.5 and fs._material_vol() == 9.0
        assert rec._setting("sleeve_drift_band_pts", rec.SLEEVE_DRIFT_BAND_PCT) == 1.5
        assert rec._setting("ignore_mute_days", rec._DISMISS_TTL_DAYS) == 3
    finally:
        thresholds._OVERRIDES.reset(tok)
    assert fs._min_move() == 2.0                                            # nothing set: as before


def test_a_hidden_card_returns_at_the_change_you_set():
    from app.services import recommendations as rec
    src = __import__("inspect").getsource(rec)
    for key in NEW - {"concentration_cap_pct", "min_resize_pts", "material_gap_close_pct", "material_vol_drop_pts"}:
        assert f'_setting("{key}"' in src or f'"{key}"' in src, f"{key} has no reader in recommendations"


def test_the_card_floor_is_never_below_the_funded_minimum():
    # Defaults: card 100 / 0.5%, funded 250. A ₪20k book: 0.5% = 100, so the floor is 250, not 100.
    assert thresholds.card_floor_ils(20_000) == 250.0
    assert thresholds.card_floor_ils(100_000) == 500.0                     # 0.5% wins on a big book
    tok = _with({"min_funded_trade_ils": 400})
    try:
        assert thresholds.card_floor_ils(20_000) == 400.0
    finally:
        thresholds._OVERRIDES.reset(tok)


def test_no_trade_card_or_headsup_below_the_funded_minimum():
    """A small commodity gap is silent -- not a "Buy ₪180 of X" with nothing to press."""
    from app.services import headsup
    card = {"id": "rec_c", "dimension": "diversification", "title": "Add a commodities sleeve",
            "action": "Buy ₪180 of DBC.", "est_amount": 180.0, "apply": {"kind": "buy_funded"}}
    cards, heads, _ = headsup.split([card], nav=20_000, actionable=lambda c: True)
    assert cards == []                                                    # under ₪250: not a card
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "VTI", "market": "NASDAQ", "asset_class": "Equities", "depth": 1,
             "spot_price": 100.0, "listing_price": 100.0, "quantity": 50, "cost_basis": 90.0}]})
        c.put("/api/v1/plan", json={"objective": "Balanced"})
        body = c.get("/api/v1/recommendations").json()
    from tests._cards import all_cards
    small = [x for x in all_cards(body) + [h for h in body.get("headsups") or []]
             if str(x.get("title", "")).startswith("Add a commodities sleeve")
             and float((x.get("card") or x).get("est_amount") or 0) < thresholds.card_floor_ils(18_550)]
    assert small == [], small


def test_plan_reports_the_effective_floor_and_23_settings():
    with TestClient(app) as c:
        p = c.get("/api/v1/plan").json()
    assert len(p["thresholds_spec"]) == 23 and NEW <= set(p["thresholds_spec"])
    assert p["thresholds_effective_card_floor_ils"] >= p["thresholds"]["min_funded_trade_ils"]
