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


# --------------------------------------------------------------------------- #
# Found on the live card after deploy (2026-09-29 23:05): SOXL's 20-day vol
# (115.7%) sat in the review ROW, but the card read `holding_review.stored()`
# (the stored backtest, no metrics) and said "no volatility measurement".
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_split_uses_the_holds_measured_volatility(ils_quotes, monkeypatch):
    import math as _m

    from app.agents.screener_agent import OpportunityAgent
    closes = [100.0 * (1 + 0.04 * _m.sin(i / 2.0)) for i in range(260)]
    monkeypatch.setattr(OpportunityAgent, "_history", lambda self, t, n: (closes, "ok"))
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "held_vol@example.com")
            c, built = await _card(s, user)
            soxl = next(r for r in built["holding_review"] if r["ticker"] == "SOXL")
            assert soxl["metrics"]["vol_20d_pct"] > 0, "setup: the review measured it"
            rd = c["apply"]["options"]["redeploy"]
            assert rd["touch_probability"] is not None, rd["split_why"]
            assert "No volatility measurement" not in rd["split_why"]
    finally:
        await eng.dispose()


def test_plan_legs_are_labelled_honestly_and_never_suggested_above_their_limit():
    holds = {"SOXL": {"level": 142.29, "price_now": 146.93, "vol_20d_pct": None}}
    legs = [{"ticker": "VTI", "amount_ils": 627.92, "asset_class": "Equities",
             "reason": "at plan weight"},
            {"ticker": "BND", "amount_ils": 400.0, "asset_class": "Fixed Income",
             "reason": "under"}]
    c = rr._held_cash_card(holds, 1713.49, 1863.0, 1500.0, nav=23474.46,
                           mix={"Equities": 0.693, "Fixed Income": 0.10},
                           target={"Equities": 0.6871, "Fixed Income": 0.1214},
                           under_legs=legs, ideas=[])
    d = {x["ticker"]: x for x in c["apply"]["options"]["redeploy"]["destinations"]}
    assert d["VTI"]["plan_label"] == "keeps the plan's mix"
    assert d["BND"]["plan_label"] == "under target"
    for x in d.values():
        assert x["suggested_ils"] <= x["max_ils"] + 1e-9, x


@pytest.mark.asyncio
async def test_the_rule_row_does_not_call_the_plan_gap_cash(ils_quotes):
    from app.services import rules_service as rs
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "held_status@example.com")
            row = next(r for r in await rs.list_rules(s, user) if r["rule_type"] == "buy_back")
            assert "of cash waiting" not in (row.get("status") or ""), row.get("status")
    finally:
        await eng.dispose()


def test_a_likely_touch_recommends_waiting_but_still_suggests_amounts():
    """Eran 2026-09-29 23:42, on the live card (every box 0): "I asked you to
    suggest the amounts". The touch chance picks the RECOMMENDED option; the
    elsewhere tile is always pre-filled -- plan legs first, then half of the rest
    across the top two ideas. His live numbers: 620 / 270 / 270, keep 553."""
    holds = {"SOXL": {"level": 142.29, "price_now": 146.93, "vol_20d_pct": 115.7}}
    legs = [{"ticker": "VTI", "amount_ils": 627.92, "asset_class": "Equities",
             "reason": "at plan weight"}]
    ideas = [{"ticker": "USO", "name": "Crude oil", "score": 85.4, "reasons": ["up 26%"]},
             {"ticker": "BND", "name": "Total US bond", "score": 83.8, "reasons": ["6y"]},
             {"ticker": "AGG", "name": "Agg", "score": 83.8, "reasons": ["6y"]}]
    c = rr._held_cash_card(holds, 1713.49, 1863.0, 1500.0, nav=23474.46,
                           mix={"Equities": .693, "Commodities": .1008, "Fixed Income": .1268},
                           target={"Equities": .6871, "Commodities": .10, "Fixed Income": .1214},
                           under_legs=legs, ideas=ideas)
    rd = c["apply"]["options"]["redeploy"]
    got = {d["ticker"]: d["suggested_ils"] for d in rd["destinations"]}
    assert got == {"VTI": 620.0, "USO": 270.0, "BND": 270.0, "AGG": 0.0}, got
    assert rd["suggested_keep_ils"] == pytest.approx(553.49)
    assert rd["touch_probability"] > 0.9
    assert c["apply"]["recommended"] == "keep", "92%: the pick is to wait"
    assert "keep waiting" in rd["split_why"] and "VTI first" in rd["split_why"]


def test_an_unlikely_touch_recommends_putting_it_to_work():
    holds = {"SOXL": {"level": 100.0, "price_now": 150.0, "vol_20d_pct": 30.0}}
    c = rr._held_cash_card(holds, 1000.0, 1500.0, 1200.0, nav=20000.0,
                           mix={"Equities": .6}, target={"Equities": .7},
                           under_legs=[{"ticker": "VTI", "amount_ils": 400.0,
                                        "asset_class": "Equities", "reason": "under"}],
                           ideas=[])
    assert c["apply"]["recommended"] == "redeploy"
    assert c["apply"]["options"]["redeploy"]["destinations"][0]["suggested_ils"] == 400.0


# --------------------------------------------------------------------------- #
# PR 86 -- production 2026-09-30 00:21, Eran: "I chose to re-invest the money
# waiting for SOXL but now, I'm getting this card to undo my action" ("Sell
# Equities ₪758"). Two faults, both measured live:
#   1. the book target still parked the WHOLE SOXL gap (8.5%, ₪1,996) in Cash
#      although only ₪553 of cash was left above the floor -- so the rebalancer
#      asked him to sell what he had just bought to rebuild it;
#   2. the accept returned before the ledger boundary: three buys, zero rows.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_investing_part_elsewhere_is_recorded_and_not_undone(ils_quotes, monkeypatch):
    from sqlalchemy import select

    from app.models.tables import Transaction
    from app.services.book_target import book_target
    from app.services.fx import book_nav_ils
    ideas = [{"ticker": "CVX", "name": "Chevron", "score": 73.2, "reasons": ["value"]},
             {"ticker": "JPM", "name": "JPMorgan", "score": 70.1, "reasons": ["ROE"]}]
    monkeypatch.setattr(rr, "_buy_ideas", lambda *a, **k: ideas)
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "held_undo@example.com")
            c, _ = await _card(s, user)
            reserved = c["apply"]["reserved_ils"]
            alloc = "CVX:700,JPM:600"
            out = await rr.apply_recommendation(s, user, c["id"], choice="redeploy", alloc=alloc)
            assert out["applied"] == "held_cash_redeploy", out
            # 2. every buy is in the ledger, once, under this card
            rows = (await s.execute(select(Transaction).where(
                Transaction.subject == user.email, Transaction.txn_type == "Buy"))).scalars().all()
            got = sorted(r.ticker for r in rows if "held_cash" in (r.source or ""))
            assert got == ["CVX", "JPM"], [(r.ticker, r.source) for r in rows]
            assert not out.get("unrecorded"), out.get("unrecorded")
            assert abs(float(out.get("nav_drift_ils") or 0.0)) < 1.0, out
            # 1. the reserve the target keeps is the cash that is left for it
            nav = book_nav_ils(await list_positions(s, user))
            bt = await book_target(s, user, "Grow")
            kept_ils = sum(bt["held_back"].values()) * nav
            spendable = (await get_cash(s, user)) - 150.0
            assert kept_ils <= spendable + 1.0, (kept_ils, spendable)
            # the hold's gap (~₪1,545) is larger than the ₪413 left above the floor,
            # so all of that cash -- and no more -- stays reserved for SOXL
            assert reserved > spendable
            assert kept_ils == pytest.approx(spendable, abs=2.0)
            # ... so nothing asks him to SELL the class he just bought into
            # (CVX, JPM: Equities) to rebuild cash he chose not to keep. The
            # fixture's own pre-existing Fixed Income overweight (BND 15.9% vs
            # 5.9%) may still surface a rebalance -- that is not an undo.
            built = await rr.build_recommendations(s, user)
            undo = []
            for r in built["recommendations"] + built.get("more_recommendations", []):
                ap = r.get("apply") or {}
                if ap.get("kind") != "rebalance_to_objective":
                    continue
                legs = ap.get("legs") or [{"asset_class": ap.get("asset_class"),
                                            "direction": ap.get("direction")}]
                undo += [lg for lg in legs if lg.get("asset_class") == "Equities"
                         and lg.get("direction") == "sell"]
            assert undo == [], undo
    finally:
        await eng.dispose()


def test_cap_held_scales_to_the_cash_that_exists():
    from app.services.book_target import cap_held
    assert cap_held({"SOXL": 0.085}, 0.0236) == pytest.approx({"SOXL": 0.0236})
    assert cap_held({"A": 0.02, "B": 0.02}, 0.02) == pytest.approx({"A": 0.01, "B": 0.01})
    assert cap_held({"SOXL": 0.01}, 0.5) == {"SOXL": 0.01}
    assert cap_held({"SOXL": 0.05}, 0.0) == {}


@pytest.mark.asyncio
async def test_no_card_spends_the_cash_kept_for_the_hold(ils_quotes, monkeypatch):
    """PR 87, production 2026-09-30 00:59, Eran: "it offers me to buy BND using
    cash but if I do that I won't be able to buy-back SOXL". "Add to BND ₪841"
    took ₪553.49 from cash -- the exact reserve he kept. The reserve is now
    committed to the build's shared FundingLedger before any card is planned."""
    ideas = [{"ticker": "CVX", "name": "Chevron", "score": 73.2, "reasons": ["value"]},
             {"ticker": "JPM", "name": "JPMorgan", "score": 70.1, "reasons": ["ROE"]}]
    monkeypatch.setattr(rr, "_buy_ideas", lambda *a, **k: ideas)
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "held_keep@example.com")
            c, _ = await _card(s, user)
            await rr.apply_recommendation(s, user, c["id"], choice="redeploy",
                                          alloc="CVX:700,JPM:600")
            built = await rr.build_recommendations(s, user)
            reserve = sum(built["book_target"]["held_back"].values()) * \
                book_nav_ils_rows(await list_positions(s, user))
            assert reserve > 250, "setup: something is still kept for SOXL"
            spent = [(r["id"], r["title"], (r.get("apply") or {}).get("from_cash_ils"))
                     for r in built["recommendations"] + built.get("more_recommendations", [])
                     if not str(r["id"]).startswith("rule_")
                     and float((r.get("apply") or {}).get("from_cash_ils") or 0) > 1.0]
            assert spent == [], spent
    finally:
        await eng.dispose()


def book_nav_ils_rows(rows):
    from app.services.fx import book_nav_ils
    return book_nav_ils(rows)
