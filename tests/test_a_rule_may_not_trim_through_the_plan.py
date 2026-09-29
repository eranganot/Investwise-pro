"""PR 79 -- a rule may not trim a holding through its own plan's target; rules are editable.

Eran, 2026-09-29: the VTI card "VTI is 18% of your portfolio -- trim to 15%, the
rule you set". The plan targets VTI at 18.2% (60% of a 30.4% Classic 60/40 core).
The ledger shows the loop: the core realign bought VTI (09-26 12:26, 09-29 07:42)
and this cap sold it an hour later (09-26 13:14, 09-29 08:46) -- tax each time.
PR 72 made rule-vs-SLEEVE conflicts a decision; the core was never checked.

His decisions: SELL keeps the target and holds re-buys (as PR 75 does for
sleeves); KEEP updates the rule; a cap below the plan target WARNS and lets him
save; the edit covers level + note.

Every choice is APPLIED and the rule / book re-measured.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.models.tables import TradingRule  # noqa: E402
from app.schemas.intake import IntakePosition  # noqa: E402
from app.schemas.state_machine import Market  # noqa: E402
from app.services import recommendations as rr  # noqa: E402
from app.services import rules_service as rs  # noqa: E402
from app.services import sleeve_service as sv  # noqa: E402
from app.services.feed_service import ensure_user  # noqa: E402
from app.services.intake_service import (  # noqa: E402
    ensure_account, ensure_entity, upsert_positions)
from app.services.plan_service import upsert_plan  # noqa: E402


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


def _pos(ticker, qty, price, basis, cls):
    # TASE + ILS keeps FX out of the arithmetic.
    return IntakePosition(ticker=ticker, market=Market.TASE, depth=2, spot_price=price,
                          listing_price=price, quantity=qty, cost_basis=basis,
                          asset_class=cls)


async def _core_book(s, email, *, vti_qty=100.0, cap=10.0, rule_type="max_weight"):
    """A Classic 60/40 core holding VTI and BND, with one latched rule on VTI."""
    user = await ensure_user(s, email)
    entity = await ensure_entity(s, user, "Personal", "Personal")
    account = await ensure_account(s, entity, "Main")
    await upsert_positions(s, account, [_pos("VTI", vti_qty, 100.0, 90.0, "Equities"),
                                        _pos("BND", 100.0, 100.0, 100.0, "Fixed Income")])
    await s.commit()
    await upsert_plan(s, user, objective="Balanced", risk_tolerance="Medium",
                      horizon_years=6, target_amount=100000)
    res = await sv.set_core(s, user, "bal_6040")
    assert res.get("ok", True), res
    await s.commit()
    mode = "pct" if rule_type == "max_weight" else "price"
    rule = await rs.create_rule(s, user, ticker="VTI", rule_type=rule_type, mode=mode,
                                level=cap)
    rule.triggered = True
    await s.commit()
    return user, rule


async def _cards(s, user, btgt=None):
    from app.services.fx import book_nav_ils
    from app.services.intake_service import list_positions
    rows = await list_positions(s, user)
    nav = book_nav_ils(rows)
    w = {}
    for p in rows:
        w[p.ticker.upper()] = w.get(p.ticker.upper(), 0.0) + (
            float(p.quantity) * float(p.current_price)) / nav
    cards = await rs.triggered_rule_recs(s, user)
    if btgt is None:
        btgt = await rr._book_target(s, user, "Balanced")
    return await rr._rule_vs_plan_decisions(s, user, cards, w, nav=nav, btgt=btgt), w, btgt


@pytest.mark.asyncio
async def test_a_cap_that_trims_a_core_holding_below_its_target_becomes_a_decision():
    eng, Session = _session()
    try:
        async with Session() as s:
            user, rule = await _core_book(s, "core_conflict@example.com")
            t = (await rr.plan_ticker_targets(s, user)).get("VTI")
            assert t and t > 0.10, f"the core must give VTI a target above the 10% cap: {t}"
            recs, w, _ = await _cards(s, user)
    finally:
        await eng.dispose()
    card = next(r for r in recs if r["id"] == f"rule_{str(rule.id)[:8]}")
    ap = card["apply"]
    assert ap["kind"] == "rule_vs_sleeve" and ap["conflict"] == "core", ap
    assert card["decision"] is True
    keep = ap["options"]["keep"]
    assert keep["rule_type"] == "max_weight" and keep["old_level"] == 10.0
    assert keep["level"] == rr.cap_keep_level(w["VTI"] * 100, t * 100)
    assert keep["level"] > max(w["VTI"], t) * 100, "Keep must lift the cap above the plan"


@pytest.mark.asyncio
async def test_keep_moves_the_cap_above_the_plan_and_the_rule_stops_firing():
    eng, Session = _session()
    try:
        async with Session() as s:
            user, rule = await _core_book(s, "core_keep@example.com")
            recs, _w, btgt = await _cards(s, user)
            card = next(r for r in recs if r["id"] == f"rule_{str(rule.id)[:8]}")
            new = card["apply"]["options"]["keep"]["level"]
            res = await rs.keep_and_relevel(s, user, str(rule.id), new)
            assert res["ok"], res
            r2 = await s.get(TradingRule, rule.id)
            assert float(r2.level) == new and r2.triggered is False and r2.active is True
            fired = await rs.evaluate_user(s, user, notify=False)
            assert not [f for f in fired if f.get("ticker") == "VTI"], fired
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_a_trim_that_stays_above_the_plan_is_left_alone():
    """VTI far over its target, cap still above the target: the trim is right."""
    eng, Session = _session()
    try:
        async with Session() as s:
            user, rule = await _core_book(s, "core_over@example.com", vti_qty=900.0)
            t = (await rr.plan_ticker_targets(s, user))["VTI"]
            r = await s.get(TradingRule, rule.id)
            r.level = round(t * 100 + 5, 1)
            await s.commit()
            recs, _w, _ = await _cards(s, user)
    finally:
        await eng.dispose()
    card = next(r for r in recs if r["id"] == f"rule_{str(rule.id)[:8]}")
    assert card["apply"]["kind"] == "trim", "a trim that stays above the plan must stand"


@pytest.mark.asyncio
async def test_a_protective_stop_is_never_turned_into_a_negotiation():
    eng, Session = _session()
    try:
        async with Session() as s:
            user, rule = await _core_book(s, "core_stop@example.com",
                                          cap=150.0, rule_type="stop_loss")
            recs, _w, _ = await _cards(s, user)
    finally:
        await eng.dispose()
    for r in recs:
        assert not r.get("decision"), "a stop-loss must overrule a target, not ask"


@pytest.mark.asyncio
async def test_the_realign_does_not_rebuy_a_ticker_on_hold():
    from app.services import core_realign
    plan = core_realign.plan_realign(
        basket={"VTI": 0.6, "BND": 0.4}, core_pct=1.0, nav=10000.0,
        held={"VTI": 1000.0, "BND": 4000.0}, cash_ils=5000.0, cash_floor_ils=0.0,
        no_buy={"VTI"})
    assert not [leg for leg in plan["legs"]
                if leg["action"] == "buy" and leg["ticker"] == "VTI"], plan["legs"]


@pytest.mark.asyncio
async def test_edit_changes_level_and_note_and_re_arms():
    eng, Session = _session()
    try:
        async with Session() as s:
            user, rule = await _core_book(s, "edit_rule@example.com")
            t = (await rr.plan_ticker_targets(s, user))["VTI"] * 100
            # Below the plan: refused once, with a suggestion.
            out = await rs.update_rule(s, user, str(rule.id), level=12.0)
            assert out["ok"] is False and out["needs_confirm"] is True
            assert out["suggested_level"] > t
            r = await s.get(TradingRule, rule.id)
            assert float(r.level) == 10.0, "a warning must not save"
            # Forced: saved.
            out = await rs.update_rule(s, user, str(rule.id), level=12.0, note="mine",
                                       force=True)
            assert out["ok"] and out["level"] == 12.0
            # Above the plan: saved straight away, re-armed, note kept.
            out = await rs.update_rule(s, user, str(rule.id), level=round(t + 5, 1))
            assert out["ok"], out
            r = await s.get(TradingRule, rule.id)
            assert float(r.level) == round(t + 5, 1) and r.triggered is False
            assert r.note == "mine"
            bad = await rs.update_rule(s, user, str(rule.id), level=150.0)
            assert bad["ok"] is False and "below 100" in bad["error"]
            assert (await rs.update_rule(s, user, "nope", level=5))["error"] == "rule not found"
    finally:
        await eng.dispose()


def test_the_patch_route_exists():
    from fastapi.testclient import TestClient

    import app.main as m
    with TestClient(m.app) as c:
        r = c.patch("/api/v1/rules/00000000-0000-0000-0000-000000000000", json={"level": 5})
        assert r.status_code == 404, r.text
