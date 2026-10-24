"""A3r (D132): one exit per holding -- the protection manager.

Live 2026-10-24: 39 active rules; VTI, VXUS, QQQ, SMH, XLE, COMT, USO, UGA, UGL and BIL each
carried a fixed stop ~8% down, a 10-17% trailing stop and a take-profit, armed by five
generators. Owner: core funds "Bands + book guard only"; sleeve / 2x-3x holdings one exit
"From measured volatility"; take-profits "Drop them".

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import math  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from app.services import protection_manager as pm  # noqa: E402


def _r(i, tk, rt, level, mode="pct", active=True):
    return {"id": f"r{i}", "ticker": tk, "rule_type": rt, "mode": mode, "level": level, "active": active}


RULES = [_r(1, "VTI", "stop_loss", 345.5, "price"), _r(2, "VTI", "trailing_stop", 10.0),
         _r(3, "VTI", "take_profit", 415.57, "price"), _r(4, "VTI", "rebalance_band", 3.0),
         _r(5, "BIL", "stop_loss", 84.25, "price", active=False),
         _r(6, "TQQQ", "stop_loss", 56.35, "price"), _r(7, "TQQQ", "take_profit", 50.0),
         _r(8, "TQQQ", "trailing_stop", 20.0), _r(9, "UGL", "trailing_stop", 12.0, active=False),
         _r(10, "SOXL", "buy_back", 82.11, "price")]
ROLES = {"VTI": "core", "BIL": "core", "TQQQ": "sleeve", "UGL": "sleeve", "SOXL": "sleeve", "DBMF": "sleeve"}
VOLS = {"TQQQ": 18.5, "UGL": 9.0, "SOXL": 25.0, "DBMF": None}


# --------------------------------------------------------------------------- #
# pure
# --------------------------------------------------------------------------- #
def test_core_funds_lose_every_exit_and_sleeves_keep_one_trailing_stop_sized_from_volatility():
    ch = pm.plan_changes(RULES, ROLES, VOLS, 2.0)
    deleted = {d["rule_id"] for d in ch["delete"]}
    assert deleted == {"r1", "r2", "r3", "r5", "r6", "r7"}, "core exits (even switched off) and TQQQ's extras go"
    assert "r4" not in deleted and "r10" not in deleted, "a band and a buy-back are not exits"
    edits = {e["ticker"]: e["to_level"] for e in ch["edit"]}
    assert edits == {"TQQQ": 37.0, "UGL": 18.0}, "2 x 18.5; a switched-off trail is re-levelled and armed"
    assert ch["create"] == [{"ticker": "SOXL", "rule_type": "trailing_stop", "mode": "pct", "level": 50.0,
                             "vol_pct": 25.0, "k": 2.0}]
    assert ch["no_vol"] == ["DBMF"], "no history: left as it is"


def test_the_width_is_clamped_and_an_already_managed_book_has_nothing_to_change():
    assert pm.trail_for(40.0, 4.0) == pm.MAX_TRAIL_PCT and pm.trail_for(0.5, 2.0) == pm.MIN_TRAIL_PCT
    done = [_r(1, "TQQQ", "trailing_stop", 37.0), _r(2, "VTI", "rebalance_band", 3.0)]
    assert pm.is_empty(pm.plan_changes(done, {"TQQQ": "sleeve", "VTI": "core"}, {"TQQQ": 18.5}, 2.0))


def test_monthly_volatility_is_measured_from_the_holdings_own_prices():
    closes = [100.0 * math.exp(0.01 * ((-1) ** i)) for i in range(300)]
    v = pm.monthly_vol_pct(closes)
    assert v is not None and 8.0 < v < 11.0, v
    assert pm.monthly_vol_pct(closes[:30]) is None, "under 60 sessions is not a measurement"


def test_no_generated_card_arms_an_exit_and_suggestions_carry_none():
    recs = [{"id": "a", "apply": {"kind": "create_rules", "rules": [{"rule_type": "stop_loss"}]}},
            {"id": "b", "apply": {"kind": "create_rules", "rules": [{"rule_type": "trailing_stop"},
                                                                    {"rule_type": "max_weight"}]}},
            {"id": "c", "apply": {"kind": "create_rules", "rules": [{"rule_type": "rebalance_band"}]}},
            {"id": "d", "apply": {"kind": "create_rule", "rule_type": "take_profit"}},
            {"id": "e", "apply": {"kind": "buy_funded"}}]
    kept, dropped = pm.filter_generated(recs)
    assert [c["id"] for c in kept] == ["c", "e"] and dropped == ["a", "b", "d"]
    sugg = [{"ticker": "VTI", "rules": [{"rule_type": "stop_loss"}, {"rule_type": "rebalance_band"}]},
            {"ticker": "QQQ", "rules": [{"rule_type": "take_profit"}]}]
    assert pm.filter_suggestions(sugg) == [{"ticker": "VTI", "rules": [{"rule_type": "rebalance_band"}]}]


# --------------------------------------------------------------------------- #
# measured, carded, accepted
# --------------------------------------------------------------------------- #
def _pos(ticker, ils, cls="Equities"):
    px = ils / 100.0
    return {"ticker": ticker, "market": "NASDAQ", "asset_class": cls, "depth": 3,
            "spot_price": px, "listing_price": px, "quantity": 100.0, "cost_basis": px / 1.2,
            "expected_return_pct": 8, "volatility_pct": 20}


def _fake_preview(amounts: dict, halves: dict):
    """Today's rules, then one preview per k: the TQQQ trail's width decides the amount."""
    async def fake(session, user, *, armed=None, extra_rules=None, **kw):
        lvl = next((x["level"] for x in extra_rules or [] if x["ticker"] == "TQQQ"), None)
        k = None if lvl is None else round(lvl / 18.5)
        return {"ok": True, "headline": {"p_goal_pct": 1.0}, "amount_at_confidence": amounts[k],
                "roi_at_confidence_pct": amounts[k] / 5000.0, "p_halve_pct": halves[k], "median": 1.0,
                "worst5_mean": 1.0}
    return fake


async def _seed(c, own_session):
    from app.services.feed_service import ensure_superadmin
    from app.services.rules_service import create_rule
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("TQQQ", 4000.0), _pos("VTI", 3000.0), _pos("BIL", 1000.0, "Fixed Income")]})
    c.put("/api/v1/plan", json={"objective": "Grow", "target_roi_pct": 30, "target_date": "2031"})
    async with own_session() as s:
        u = await ensure_superadmin(s)
        for tk, rt, mode, lvl in (("VTI", "stop_loss", "price", 27.0), ("VTI", "trailing_stop", "pct", 10.0),
                                  ("VTI", "take_profit", "price", 33.0), ("BIL", "stop_loss", "price", 9.0),
                                  ("TQQQ", "stop_loss", "price", 36.0), ("TQQQ", "trailing_stop", "pct", 20.0)):
            await create_rule(s, u, ticker=tk, rule_type=rt, mode=mode, level=lvl)
        await s.commit()


async def test_the_manager_measures_each_width_and_accept_leaves_one_exit_per_holding(own_session, monkeypatch):
    from sqlalchemy import select

    from app.models.tables import RuleEvent, TradingRule
    from app.services import frontier_service as fs
    from app.services.feed_service import ensure_superadmin
    real_inputs = pm._inputs

    async def inputs(session, user):
        got = await real_inputs(session, user)
        return {**got, "vols": {tk: 18.5 for tk in got["vols"]}}

    monkeypatch.setattr(pm, "_inputs", inputs)
    # k=3 reaches the most inside the limit; k=4 more, but halves the book too often.
    monkeypatch.setattr(fs, "preview", _fake_preview({None: 25000.0, 2: 26000.0, 3: 28000.0, 4: 31000.0},
                                                     {None: 0.8, 2: 0.9, 3: 1.0, 4: 60.0}))
    try:
        with TestClient(m.app) as c:
            await _seed(c, own_session)
            r = c.post("/api/v1/plan/protection/refresh").json()
            assert r.get("stored"), r
            assert r["roles"] == {"BIL": "core", "TQQQ": "sleeve", "VTI": "core"}
            assert r["k"] == 3.0 and r["within_halving"] is True
            assert [t["k"] for t in r["tried"]] == [2.0, 3.0, 4.0]
            assert c.get("/api/v1/plan/protection").json()["as_of"] == r["as_of"]
            j = c.get("/api/v1/recommendations").json()
            cards = [x for x in j["recommendations"] + j.get("more_recommendations", [])
                     if x["id"] == pm.CARD_ID]
            assert len(cards) == 1, [x["id"] for x in j["recommendations"]]
            card = cards[0]
            assert card["title"] == "One exit per holding: remove 5 rules, set 1 trailing stop"
            assert "₪25,000 -> ₪28,000" in card["why"] and "halves 0.8% -> 1%" in card["why"]
            assert "Set: TQQQ trailing stop 20% -> 55.5% (3x its monthly volatility, 18.5%)" in card["how"]
            assert not any(x["id"] == "rec_goal_protect_book" for x in j["recommendations"])
            out = c.post(f"/api/v1/recommendations/{pm.CARD_ID}/accept")
            assert out.status_code == 200, out.text
            rules = c.get("/api/v1/rules").json()["rules"]
            exits = sorted((x["ticker"], x["rule_type"], float(x["level"]), x["active"]) for x in rules
                           if x["rule_type"] in pm.EXIT_TYPES)
            assert exits == [("TQQQ", "trailing_stop", 55.5, True)], exits
            j = c.get("/api/v1/recommendations").json()
            assert not any(x["id"] == pm.CARD_ID for x in j["recommendations"]), "the rule set it measured is gone"
        async with own_session() as s:
            u = await ensure_superadmin(s)
            ev = (await s.scalars(select(RuleEvent).where(RuleEvent.subject == u.email))).all()
            outs = sorted(e.outcome for e in ev if (e.action or {}).get("card") == pm.CARD_ID)
            assert outs == ["deleted"] * 5 + ["edited"]
            left = (await s.scalars(select(TradingRule).where(TradingRule.subject == u.email))).all()
            assert len(left) == 1
    finally:
        from tests.test_frontier_goal import _reset_plan
        await _reset_plan(own_session)


async def test_accept_refuses_whole_when_a_rule_moved_since_it_was_measured(own_session):
    import uuid

    from app.models.tables import TradingRule
    from app.services.feed_service import ensure_user
    async with own_session() as s:
        u = await ensure_user(s, "a3r@example.com")
        a = TradingRule(id=uuid.uuid4(), subject=u.email, ticker="VTI", rule_type="stop_loss", mode="price",
                        level=30.0, active=True)
        b = TradingRule(id=uuid.uuid4(), subject=u.email, ticker="VTI", rule_type="take_profit", mode="price",
                        level=40.0, active=True)
        s.add_all([a, b])
        await s.commit()
        spec = {"delete": [{"rule_id": str(a.id), "ticker": "VTI", "rule_type": "stop_loss", "from_level": 30.0},
                           {"rule_id": str(b.id), "ticker": "VTI", "rule_type": "take_profit", "from_level": 41.0}],
                "edit": [], "create": []}
        out = await pm.apply(s, u, spec)
        assert out["ok"] is False and out["refused"] == "RULES_CHANGED"
        assert await s.get(TradingRule, a.id) is not None, "nothing was deleted"


async def test_a_book_with_nothing_to_change_has_no_card(own_session, monkeypatch):
    from app.services.feed_service import ensure_superadmin

    async def inputs(session, user):
        return {"roles": {"VTI": "core"}, "rules": [_r(1, "VTI", "rebalance_band", 3.0)], "vols": {}}

    monkeypatch.setattr(pm, "_inputs", inputs)
    try:
        with TestClient(m.app) as c:
            c.put("/api/v1/plan", json={"objective": "Grow", "target_roi_pct": 30, "target_date": "2031"})
        async with own_session() as s:
            r = await pm.refresh(s, await ensure_superadmin(s))
            assert r["stored"] and r["changes"] is None
            assert await pm.card(s, await ensure_superadmin(s)) == []
    finally:
        from tests.test_frontier_goal import _reset_plan
        await _reset_plan(own_session)


async def test_a_real_measurement_on_the_goal_model(own_session):
    """No stand-ins: the preview replays the book with the exits removed, re-levelled and added."""
    from app.services.feed_service import ensure_superadmin
    from app.services.rules_service import create_rule
    from tests.test_frontier_goal import _reset_plan, _seed
    try:
        with TestClient(m.app) as c:
            held = _seed(c, 30)
            c.post("/api/v1/portfolio/contributions", json={"amount_ils": 20000, "mode": "set"})
            tks = sorted({p["ticker"] for p in c.get("/api/v1/portfolio").json()["positions"]} - {"CASH"})
            async with own_session() as s:
                u = await ensure_superadmin(s)
                for tk in tks:
                    await create_rule(s, u, ticker=tk, rule_type="take_profit", mode="pct", level=10.0)
                await s.commit()
            r = c.post("/api/v1/plan/protection/refresh").json()
        assert r.get("ok") and r.get("stored"), ({k: r.get(k) for k in ("reason", "detail", "roles")}, held)
        assert r["changes"] and len(r["changes"]["delete"]) >= len(tks), "every take-profit goes"
        assert r["current"]["amount_at_confidence"] and r["tried"]
        assert all(t["stats"]["amount_at_confidence"] for t in r["tried"])
    finally:
        await _reset_plan(own_session)
