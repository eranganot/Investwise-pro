"""PR 83 -- cash above the trigger that a buy-back hold keeps back gets a card.

Eran, 2026-09-29 21:46: "I have more than 1500 in cash but still no
recommendation card on how to utilize it". Measured live: ₪1,863 cash, a ₪150
floor, a ₪1,500 trigger, and `book_target.held_back == {"SOXL": 0.0837}` -- the
SOXL sleeve's shortfall parked in cash by the buy-back hold his rule-vs-sleeve
SELL created at 142.29 (SOXL then 147.86). `_redeploy_cash_recs` reserved all
₪1,713 of spendable cash and returned [] in silence.

His answer: a card with a choice -- keep waiting / buy it back now / invest it
elsewhere (the hold stays). Each choice is APPLIED and the book re-measured.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.schemas.intake import IntakePosition  # noqa: E402
from app.schemas.state_machine import Market  # noqa: E402
from app.services import buyback_hold as bh  # noqa: E402
from app.services import recommendations as rr  # noqa: E402
from app.services import sleeve_service as sv  # noqa: E402
from app.services.feed_service import ensure_user  # noqa: E402
from app.services.intake_service import (  # noqa: E402
    ensure_account, ensure_entity, get_cash, list_positions, set_cash, upsert_positions)
from app.services.plan_service import upsert_plan  # noqa: E402


@pytest.fixture
def ils_quotes(monkeypatch):
    from types import SimpleNamespace as NS

    from app.providers import registry
    from app.services import pricing_service, strategy_service

    def _q(tk):
        return NS(ticker=(tk or "").upper(), price=100.0, currency="ILS", market="TASE")
    for mod in (registry, pricing_service, strategy_service):
        monkeypatch.setattr(mod, "guarded_quote", _q, raising=False)


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


def _pos(tk, qty, cls):
    return IntakePosition(ticker=tk, market=Market.TASE, depth=2, spot_price=100.0,
                          listing_price=100.0, quantity=qty, cost_basis=90.0, asset_class=cls)


async def _book(s, email):
    """SOXL sleeve at 40% target holding ~30%; SOXL on a hold at 90 (price 100)."""
    user = await ensure_user(s, email)
    ent = await ensure_entity(s, user, "Personal", "Personal")
    acc = await ensure_account(s, ent, "Main")
    await upsert_positions(s, acc, [_pos("SOXL", 60, "Equities"), _pos("VTI", 80, "Equities"),
                                    _pos("BND", 30, "Fixed Income")])
    await s.commit()
    await set_cash(s, user, 1863.0)          # the live figure
    await upsert_plan(s, user, objective="Grow", risk_tolerance="High", horizon_years=6,
                      target_amount=100000, cash_target_ils=150, cash_redeploy_at_ils=1500)
    res = await sv.add_or_resize(s, user, "btm_trend_soxl", 40.0)
    assert res["ok"], res
    await bh.record(s, user, ticker="SOXL", sold_price=90.0, shares=5, strategy_id="btm_trend_soxl")
    await s.commit()
    return user


async def _card(s, user):
    built = await rr.build_recommendations(s, user)
    cards = built["recommendations"] + built.get("more_recommendations", [])
    return next((c for c in cards if (c.get("apply") or {}).get("kind") == "held_cash"), None), built


@pytest.mark.asyncio
async def test_reserved_cash_above_the_trigger_gets_a_choice_card(ils_quotes):
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "held1@example.com")
            c, built = await _card(s, user)
            assert (built.get("book_target") or {}).get("held_back", {}).get("SOXL"), \
                "the setup must reproduce the live shape: SOXL's shortfall held back"
            assert c is not None, [x["title"] for x in built["recommendations"]]
            ap = c["apply"]
            assert ap["tickers"] == ["SOXL"] and ap["reserved_ils"] >= 250
            assert set(ap["options"]) == {"keep", "buy", "redeploy"}
            assert c["decision"] is True and "SOXL" in c["title"]
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_buy_back_ends_the_hold_and_spends_the_reserved_cash(ils_quotes):
    from app.services.fx import book_nav_ils
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "held2@example.com")
            c, _ = await _card(s, user)
            cash0 = await get_cash(s, user)
            nav0 = book_nav_ils(await list_positions(s, user))
            out = await rr.apply_recommendation(s, user, c["id"], choice="buy")
            assert out["applied"] == "held_cash_buy" and out["bought"][0]["ticker"] == "SOXL"
            assert "SOXL" not in await bh.load(s, user), "buying back ends the hold"
            spent = cash0 - await get_cash(s, user)
            assert abs(spent - c["apply"]["reserved_ils"]) < 1.0
            assert abs(book_nav_ils(await list_positions(s, user)) - nav0) < 1.0
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_keep_changes_nothing(ils_quotes):
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "held3@example.com")
            c, _ = await _card(s, user)
            cash0 = await get_cash(s, user)
            out = await rr.apply_recommendation(s, user, c["id"], choice="keep")
            assert out["applied"] == "keep_waiting"
            assert await get_cash(s, user) == cash0 and "SOXL" in await bh.load(s, user)
            out2 = await rr.apply_recommendation(s, user, c["id"], choice=None)
            assert out2.get("needs_choice") and out2.get("ok") is False
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_elsewhere_lists_places_with_reasons_and_invests_only_what_you_set(ils_quotes,
                                                                                 monkeypatch):
    """Eran: see the other places and why; invest some, hold some; actionable."""
    ideas = [{"ticker": "CVX", "name": "Chevron", "score": 73.2,
              "reasons": ["Trades near book value (P/B 2.1)", "Low debt"]},
             {"ticker": "JPM", "name": "JPMorgan", "score": 70.1, "reasons": ["ROE 15%"]},
             {"ticker": "SOXL", "name": "SOXL", "score": 99.0, "reasons": ["held"]}]
    monkeypatch.setattr(rr, "_buy_ideas", lambda *a, **k: ideas)
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "held4@example.com")
            c, _ = await _card(s, user)
            rd = c["apply"]["options"]["redeploy"]
            places = {d["ticker"]: d for d in rd["destinations"]}
            assert "SOXL" not in places, "the held ticker is never an 'elsewhere'"
            assert places["CVX"]["why"][0].startswith("Trades near book value")
            assert "class_now_pct" in places["CVX"] and "class_target_pct" in places["CVX"]
            assert rd["split_why"], "the split must be argued"
            cash0 = await get_cash(s, user)
            out = await rr.apply_recommendation(s, user, c["id"], choice="redeploy",
                                                alloc="CVX:300")
            assert out["applied"] == "held_cash_redeploy", out
            assert [b["ticker"] for b in out["bought"]] == ["CVX"]
            assert abs((cash0 - await get_cash(s, user)) - 300) < 1.0, "only what you set"
            assert out["kept_reserved_ils"] > 0 and "SOXL" in await bh.load(s, user), \
                "the rest stays reserved; the hold stays on"
            bad = await rr.apply_recommendation(s, user, c["id"], choice="redeploy",
                                                alloc="TSLA:500")
            assert bad is None or bad.get("ok") is False
    finally:
        await eng.dispose()


def test_touch_probability_is_sane():
    assert rr._touch_probability(100, 100, 50) == 1.0
    p_near = rr._touch_probability(104, 100, 90)
    p_far = rr._touch_probability(150, 100, 90)
    assert 0 < p_far < p_near < 1
    assert rr._touch_probability(104, 100, None) is None


# --------------------------------------------------------------------------- #
# The hold is a RULE you can see, edit and end (Eran: "I don't see any rule")
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_hold_is_a_visible_editable_rule_and_fires_below_its_level(ils_quotes):
    from app.models.tables import TradingRule
    from app.services import advanced_rules as adv
    from app.services import rules_service as rs
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "hold_rule@example.com")
            rows = [r for r in await rs.list_rules(s, user) if r["rule_type"] == "buy_back"]
            assert rows and rows[0]["ticker"] == "SOXL" and rows[0]["level"] == 90.0
            # edit the buy-back price -> the hold follows it
            out = await rs.update_rule(s, user, rows[0]["id"], level=120.0)
            assert out["ok"] and (await bh.load(s, user))["SOXL"]["level"] == 120.0
            # price (100) now BELOW the level -> fires, and the card buys back
            fired = await rs.evaluate_user(s, user)
            assert [f for f in fired if f["rule_type"] == "buy_back"]
            extra = await adv.extra_cards(s, user, rows=[], snap={}, plan=None,
                                          objective="Grow", cap=0.4, cash=1863.0,
                                          book_target=None, name_caps={}, ledger=None)
            c = next(x for x in extra if x["id"] == f"rule_{rows[0]['id'][:8]}")
            assert c["apply"]["kind"] == "redeploy_cash"
            assert c["apply"]["legs"][0]["ticker"] == "SOXL"
            # dealing with it ends the hold
            await rs.resolve_rule(s, user, rows[0]["id"], "executed")
            assert "SOXL" not in await bh.load(s, user)
            # deleting a hold rule ends the hold too
            await bh.record(s, user, ticker="SOXL", sold_price=130.0, shares=1, strategy_id=None)
            await s.commit()
            rid = next(r["id"] for r in await rs.list_rules(s, user)
                       if r["rule_type"] == "buy_back" and r["active"])
            await rs.delete_rule(s, user, rid)
            assert "SOXL" not in await bh.load(s, user)
            assert await s.get(TradingRule, __import__("uuid").UUID(rid)) is None
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_an_old_kv_hold_is_migrated_into_a_rule():
    import json

    from app.models.tables import KVSetting
    eng, S = _session()
    try:
        async with S() as s:
            user = await ensure_user(s, "kv_hold@example.com")
            s.add(KVSetting(key=f"buyback_holds:{user.email}"[:80],
                            value=json.dumps({"SOXL": {"level": 142.29, "shares": 5.488}})))
            await s.commit()
            h = await bh.load(s, user)
            assert h["SOXL"]["level"] == 142.29 and h["SOXL"]["hold_rule_id"]
            assert await s.get(KVSetting, f"buyback_holds:{user.email}"[:80]) is None
    finally:
        await eng.dispose()
