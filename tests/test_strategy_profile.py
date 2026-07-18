"""Strategies must be distinguishable by more than a label.

All four Grow strategies are 100% equities by asset class and the UI showed only
a description and a ticker list, so a leveraged-Nasdaq basket looked identical to
a diversified-index one. The computed profile is what separates them.
"""
from app.services import strategies as cat
from app.services import strategy_profile as prof


def _get(sid):
    return cat.get(sid)


def test_every_strategy_gets_a_full_profile():
    for s in cat.CATALOG:
        p = prof.profile(s)
        for key in ("expected_return_pct", "volatility_pct", "est_max_drawdown_pct",
                    "concentration", "time_horizon", "uses_leverage"):
            assert key in p, f"{s['id']} missing {key}"


def test_leveraged_grow_is_riskier_than_diversified_grow():
    lev = prof.profile(_get("grow_leveraged"))
    div = prof.profile(_get("grow_diversified"))
    assert lev["uses_leverage"] is True
    assert div["uses_leverage"] is False
    assert lev["volatility_pct"] > div["volatility_pct"]
    assert lev["est_max_drawdown_pct"] > div["est_max_drawdown_pct"]


def test_the_four_grow_strategies_have_distinct_profiles():
    grow = [s for s in cat.CATALOG if s["goal"] == "Grow"]
    sigs = {(p["volatility_pct"], p["est_max_drawdown_pct"], p["concentration"])
            for p in (prof.profile(s) for s in grow)}
    # identical asset-class targets, but the profiles must not collapse to one point
    assert len(sigs) >= 3


def test_preserve_strategy_is_lower_risk_than_grow():
    pre = prof.profile(_get("pre_capital"))
    grow = prof.profile(_get("grow_ai_semis"))
    assert pre["volatility_pct"] < grow["volatility_pct"]
    assert pre["time_horizon"] != grow["time_horizon"]


def test_concentration_reflects_single_name_weight():
    ai = prof.profile(_get("grow_ai_semis"))       # mostly single names
    div = prof.profile(_get("grow_diversified"))   # broad ETFs
    assert ai["single_name_weight_pct"] > div["single_name_weight_pct"]
    assert div["effective_holdings"] >= 3


def test_diff_against_plan_reports_objective_and_risk_change():
    from types import SimpleNamespace
    plan = SimpleNamespace(objective="Preserve", risk_tolerance="Low",
                           strategy="pre_capital")
    d = prof.diff_against_plan(_get("grow_ai_semis"), plan, {"Equities": 1.0})
    assert d["objective"]["changes"] is True
    assert d["objective"]["from"] == "Preserve" and d["objective"]["to"] == "Grow"
    assert d["risk_tolerance"]["changes"] is True
    assert d["is_current"] is False


def test_diff_flags_the_currently_applied_strategy():
    from types import SimpleNamespace
    plan = SimpleNamespace(objective="Grow", risk_tolerance="High", strategy="grow_ai_semis")
    d = prof.diff_against_plan(_get("grow_ai_semis"), plan, {"Equities": 1.0})
    assert d["is_current"] is True


def test_catalog_endpoint_includes_profiles():
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    from fastapi.testclient import TestClient
    import app.main as m
    with TestClient(m.app) as c:
        by_goal = c.get("/api/v1/strategies").json()["by_goal"]
        for goal, items in by_goal.items():
            for s in items:
                assert "profile" in s and s["profile"]["volatility_pct"] > 0


def test_preview_endpoint_shows_what_changes():
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    from fastapi.testclient import TestClient
    import app.main as m
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "AAA", "market": "NYSE", "asset_class": "Equities", "depth": 2,
             "spot_price": 100, "listing_price": 100, "quantity": 100, "cost_basis": 100}]})
        c.put("/api/v1/plan", json={"objective": "Preserve", "risk_tolerance": "Low"})
        r = c.get("/api/v1/strategies/grow_ai_semis/preview").json()
        assert r["ok"]
        assert r["diff"]["objective"]["to"] == "Grow"
        assert r["diff"]["risk_tolerance"]["to"] == "High"
        assert r["strategy"]["profile"]["uses_leverage"] in (True, False)
