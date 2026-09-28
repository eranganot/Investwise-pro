"""PR 72 -- a triggered rule always has a card you can act on.

Eran, 2026-09-28, item 1: *"I have trading rule triggered but no recommendation
card (action card) to act."* Measured live on `e556b64` that morning:

  * rule `8cd9f1ff`, SOXL take-profit at 148.52, price 151.45, `triggered: true`;
  * `/recommendations` -> `rule_banner.carded == ["8cd9f1ff"]` -- the card WAS
    built -- and `contradictions` held `sells_a_sleeve_at_target` for
    `rule_8cd9f1ff`, `suppressed: true`: SOXL 40.1% against a 40% sleeve;
  * the banner was reconciled BEFORE the contradiction pass, so it counted a
    card that pass then removed, and nothing on screen said so.

And the ladder that should have moved the rule after the 09-22 sale never ran:
`resolve_rule` re-armed only on outcome "accepted", while Accept sends
"executed". The rule came back at the same 148.52 and fired again on 09-25.

His decisions (2026-09-28): the conflict is shown and he decides. SELL lowers
the sleeve by the weight sold; KEEP *updates* the rule -- it is not retired.

Every assertion on a choice below APPLIES the choice and re-measures the book,
the sleeve and the rule -- the card-claim invariant in CLAUDE.md -- rather than
reading the card's text.
"""
from __future__ import annotations

import inspect

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.tables import RuleEvent, Transaction, TradingRule
from app.schemas.intake import IntakePosition
from app.schemas.state_machine import Market
from app.services import recommendations as rr
from app.services import rules_service as rs
from app.services import sleeve_service as sv
from app.services.contradictions import check as contra
from app.services.feed_service import ensure_user
from app.services.intake_service import (
    ensure_account, ensure_entity, list_positions, upsert_positions)

SLEEVE = "btm_trend_soxl"


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


def _pos(ticker, qty, price, basis):
    # TASE + ILS prices keep FX out of the arithmetic this file is about.
    return IntakePosition(ticker=ticker, market=Market.TASE, depth=2, spot_price=price,
                          listing_price=price, quantity=qty, cost_basis=basis,
                          asset_class="Equities")


async def _the_soxl_book(s, email):
    """SOXL at 40.1% of the book on a 40% sleeve, take-profit fired below the price.

    100 SOXL at 151.45 = 15,145 of a 37,768 book = 40.1%. The rule sits at
    148.52 -- the live level -- and is latched exactly as the 30-minute job
    leaves it.
    """
    user = await ensure_user(s, email)
    entity = await ensure_entity(s, user, "Personal", "Personal")
    account = await ensure_account(s, entity, "Main")
    await upsert_positions(s, account, [_pos("SOXL", 100.0, 151.45, 110.0),
                                        _pos("VTI", 100.0, 226.23, 200.0)])
    await s.commit()
    res = await sv.add_or_resize(s, user, SLEEVE, 40.0)
    assert res["ok"], res
    await s.commit()
    rule = await rs.create_rule(s, user, ticker="SOXL", rule_type="take_profit",
                                mode="price", level=148.52)
    rule.triggered = True
    s.add(RuleEvent(subject=user.email, rule_id=str(rule.id), ticker="SOXL",
                    rule_type="take_profit", title="🎯 SOXL hit your take-profit",
                    trigger_price=151.45, target_price=148.52, outcome="triggered"))
    await s.commit()
    return user, rule


async def _decision(s, user):
    """Run the real pipeline pieces in order: rule cards -> contradictions -> decision."""
    cards = await rs.triggered_rule_recs(s, user)
    weights = {"SOXL": 15145.0 / 37768.0, "VTI": 22623.0 / 37768.0}
    targets = await sv.all_sleeve_targets(s, user)
    pre = {c["id"]: c for c in cards}
    kept, found = contra(cards, sleeve_targets=targets, nav=37768.0, weights=weights)
    return await rr._rule_conflicts_to_decisions(s, user, kept, found, pre, weights,
                                                 nav=37768.0)


# --------------------------------------------------------------------------- #
# 1. The card comes back as a decision instead of vanishing
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_soxl_take_profit_on_its_sleeve_becomes_a_decision_card():
    eng, Session = _session()
    try:
        async with Session() as s:
            user, rule = await _the_soxl_book(s, "decision_probe@example.com")
            recs, found = await _decision(s, user)
    finally:
        await eng.dispose()

    card_id = f"rule_{str(rule.id)[:8]}"
    ids = [r["id"] for r in recs]
    assert card_id in ids, "the firing must have a card on screen, not in a payload"
    d = next(r for r in recs if r["id"] == card_id)
    assert d["apply"]["kind"] == "rule_vs_sleeve"
    assert d["actionable"] is True
    sell, keep = d["apply"]["options"]["sell"], d["apply"]["options"]["keep"]
    # SELL takes the rule's own 25% slice and lowers the sleeve by what it sold:
    # 40% sleeve, 40.1% position, a quarter of it -> about 30%.
    assert sell["apply"]["kind"] == "trim" and sell["apply"]["shares"] == pytest.approx(25.0)
    assert sell["strategy_id"] == SLEEVE
    assert sell["sleeve_pct_from"] == pytest.approx(40.0)
    assert 29.5 <= sell["sleeve_pct_to"] <= 30.5, sell
    # KEEP moves the rule past today's price -- the next rung (163.37) here.
    assert keep["old_level"] == pytest.approx(148.52)
    assert keep["level"] == pytest.approx(max(rs.next_rung(148.52, "price"), 151.45 * 1.1))
    assert keep["level"] > 151.45, "a kept rule may not already be breached"
    c = next(x for x in found if x["card"] == card_id)
    assert c["suppressed"] is False and c["converted_to"] == card_id


@pytest.mark.asyncio
async def test_a_suppressed_card_that_is_not_a_rule_stays_suppressed():
    """Only rule firings become decisions; every other suppression stands."""
    eng, Session = _session()
    try:
        async with Session() as s:
            user, _rule = await _the_soxl_book(s, "not_a_rule_probe@example.com")
            plain = {"id": "rec_plain1", "title": "Trim SOXL", "severity": "LOW",
                     "apply": {"kind": "trim", "ticker": "SOXL", "shares": 5.0}}
            weights = {"SOXL": 0.401}
            targets = await sv.all_sleeve_targets(s, user)
            kept, found = contra([plain], sleeve_targets=targets, nav=37768.0, weights=weights)
            recs, found = await rr._rule_conflicts_to_decisions(
                s, user, kept, found, {"rec_plain1": plain}, weights, nav=37768.0)
    finally:
        await eng.dispose()
    assert recs == []
    assert found and found[0]["suppressed"] is True


# --------------------------------------------------------------------------- #
# 2. The banner is reconciled on the cards that are actually served
# --------------------------------------------------------------------------- #
def test_the_banner_is_reconciled_after_the_contradiction_pass():
    """The ordering WAS the bug. Pin it structurally."""
    src = inspect.getsource(rr.build_recommendations)
    contra_at = src.index("recs, contradictions = _contra(")
    convert_at = src.index("_rule_conflicts_to_decisions(")
    banner_at = src.index('rule_banner = {"triggered": [], "carded": []')
    assert contra_at < convert_at < banner_at, (
        "the banner must count cards AFTER the pass that can remove them, and "
        "after rule conflicts come back as decisions -- otherwise the heal "
        "retires the rule")


def test_a_hidden_card_is_listed_as_blocked_with_its_reason():
    src = inspect.getsource(rr.build_recommendations)
    assert '"how": "blocked"' in src and '"reason": _c.get("detail")' in src


# --------------------------------------------------------------------------- #
# 3. The ladder, and why it never ran
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_executed_take_profit_climbs_the_ladder_and_stays_armed():
    """Accept resolves a rule card twice -- `executed`, then `acknowledged`.

    Before PR 72 the first call skipped the ladder (it wanted "accepted") and
    the second switched the rule off as a one-shot.
    """
    eng, Session = _session()
    try:
        async with Session() as s:
            user, rule = await _the_soxl_book(s, "ladder_probe@example.com")
            await rs.resolve_rule(s, user, str(rule.id), "executed", {"kind": "trim"})
            after_exec = (float(rule.level), rule.active, rule.triggered)
            await rs.resolve_rule(s, user, str(rule.id)[:8], "acknowledged")
            fresh = await s.get(TradingRule, rule.id)
    finally:
        await eng.dispose()
    assert after_exec == (pytest.approx(rs.next_rung(148.52, "price")), True, False)
    assert fresh.active is True, "the second resolve must not retire the re-armed ladder"
    assert float(fresh.level) == pytest.approx(163.372)


@pytest.mark.asyncio
async def test_a_dismissed_take_profit_still_latches_off():
    eng, Session = _session()
    try:
        async with Session() as s:
            user, rule = await _the_soxl_book(s, "dismiss_probe@example.com")
            await rs.resolve_rule(s, user, str(rule.id), "dismissed")
            fresh = await s.get(TradingRule, rule.id)
    finally:
        await eng.dispose()
    assert fresh.active is False and float(fresh.level) == pytest.approx(148.52)


# --------------------------------------------------------------------------- #
# 4. KEEP updates the rule; SELL trims and lowers the sleeve -- applied, measured
# --------------------------------------------------------------------------- #
def _serve(monkeypatch, card, where="recommendations"):
    async def _fake_build(session, user):
        return {"recommendations": [card] if where == "recommendations" else [],
                "more_recommendations": [card] if where == "more" else []}
    monkeypatch.setattr(rr, "build_recommendations", _fake_build)


@pytest.mark.asyncio
async def test_keep_moves_the_rule_and_sells_nothing(monkeypatch):
    eng, Session = _session()
    try:
        async with Session() as s:
            user, rule = await _the_soxl_book(s, "keep_probe@example.com")
            recs, _f = await _decision(s, user)
            card = next(r for r in recs if r.get("decision"))
            _serve(monkeypatch, card)
            qty_before = {p.ticker: float(p.quantity) for p in await list_positions(s, user)}
            out = await rr.apply_recommendation(s, user, card["id"], choice="keep")
            fresh = await s.get(TradingRule, rule.id)
            qty_after = {p.ticker: float(p.quantity) for p in await list_positions(s, user)}
            ev = (await s.scalars(select(RuleEvent).where(
                RuleEvent.rule_id == str(rule.id)))).all()
            sleeves = {x.strategy_id: float(x.sleeve_pct) for x in await sv.list_sleeves(s, user)}
    finally:
        await eng.dispose()
    assert out["applied"] == "keep_and_relevel", out
    assert qty_after == qty_before, "keep sells nothing"
    assert sleeves[SLEEVE] == pytest.approx(40.0), "keep leaves the sleeve alone"
    assert fresh.active is True and fresh.triggered is False
    assert float(fresh.level) == pytest.approx(card["apply"]["options"]["keep"]["level"])
    assert [e.outcome for e in ev] == ["kept"]


@pytest.mark.asyncio
async def test_keep_refuses_a_level_that_would_fire_again_at_once(monkeypatch):
    eng, Session = _session()
    try:
        async with Session() as s:
            user, rule = await _the_soxl_book(s, "keep_refuse_probe@example.com")
            recs, _f = await _decision(s, user)
            card = next(r for r in recs if r.get("decision"))
            _serve(monkeypatch, card)
            out = await rr.apply_recommendation(s, user, card["id"], choice="keep", level=150.0)
            fresh = await s.get(TradingRule, rule.id)
    finally:
        await eng.dispose()
    assert out.get("ok") is False and "already reached" in out["error"]
    assert float(fresh.level) == pytest.approx(148.52), "a refused level is not written"
    assert fresh.triggered is True, "the question is still open"


@pytest.mark.asyncio
async def test_sell_trims_the_slice_lowers_the_sleeve_and_climbs_the_ladder(monkeypatch):
    eng, Session = _session()
    try:
        async with Session() as s:
            user, rule = await _the_soxl_book(s, "sell_probe@example.com")
            recs, _f = await _decision(s, user)
            card = next(r for r in recs if r.get("decision"))
            sell = card["apply"]["options"]["sell"]
            _serve(monkeypatch, card)
            out = await rr.apply_recommendation(s, user, card["id"], choice="sell")
            rows = {p.ticker: p for p in await list_positions(s, user)}
            sleeves = {x.strategy_id: float(x.sleeve_pct) for x in await sv.list_sleeves(s, user)}
            fresh = await s.get(TradingRule, rule.id)
            legs = (await s.scalars(select(Transaction).where(
                Transaction.ticker == "SOXL"))).all()
    finally:
        await eng.dispose()
    assert out["applied"] == "trim", out
    # What the card claimed, re-measured:
    assert float(rows["SOXL"].quantity) == pytest.approx(100.0 - sell["apply"]["shares"])
    assert sleeves[SLEEVE] == pytest.approx(sell["sleeve_pct_to"])
    assert out["sleeve"]["ok"] and out["sleeve"]["previous_pct"] == pytest.approx(40.0)
    assert float(rows["CASH"].quantity) > 0, "net proceeds land in cash"
    assert [(t.txn_type, float(t.quantity)) for t in legs] == [("Sell", pytest.approx(25.0))]
    assert float(fresh.level) == pytest.approx(rs.next_rung(148.52, "price")) and fresh.active


@pytest.mark.asyncio
async def test_a_decision_without_a_choice_changes_nothing(monkeypatch):
    eng, Session = _session()
    try:
        async with Session() as s:
            user, rule = await _the_soxl_book(s, "nochoice_probe@example.com")
            recs, _f = await _decision(s, user)
            card = next(r for r in recs if r.get("decision"))
            _serve(monkeypatch, card)
            out = await rr.apply_recommendation(s, user, card["id"])
            fresh = await s.get(TradingRule, rule.id)
            qty = {p.ticker: float(p.quantity) for p in await list_positions(s, user)}
    finally:
        await eng.dispose()
    assert out["ok"] is False and out["needs_choice"] is True
    assert fresh.triggered is True and qty["SOXL"] == pytest.approx(100.0)


def test_the_accept_route_does_not_mark_an_unanswered_decision_done():
    from app.api.routes import recommendations as route
    src = inspect.getsource(route.accept_recommendation)
    assert src.index('result.get("ok") is False') < src.index("complete_recommendation(")


# --------------------------------------------------------------------------- #
# 5. A card under "N more lower-priority suggestions" can be accepted
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_accept_finds_a_card_in_the_lower_priority_list(monkeypatch):
    eng, Session = _session()
    try:
        async with Session() as s:
            user, _rule = await _the_soxl_book(s, "more_probe@example.com")
            card = {"id": "rec_more01", "title": "Guidance", "severity": "LOW",
                    "apply": {"kind": "none"}}
            _serve(monkeypatch, card, where="more")
            out = await rr.apply_recommendation(s, user, "rec_more01")
    finally:
        await eng.dispose()
    assert out is not None, "a card on screen must not answer 'no longer applies'"


# --------------------------------------------------------------------------- #
# 6. Through the FULL Today build -- found in independent review of this PR
# --------------------------------------------------------------------------- #
# Moving the banner below the contradiction pass made a rule whose card was
# BLOCKED look like an orphan, and the heal acknowledged it. Two shapes, both
# reproduced through `build_recommendations`: a cap on a sleeve ticker (a stale
# cap; max_weight cannot become a decision), and a take-profit whose conversion
# failed. Source-order tests could not see either, so these run the real build.
@pytest.mark.asyncio
async def test_the_full_build_serves_the_decision_and_the_banner_counts_it():
    eng, Session = _session()
    try:
        async with Session() as s:
            user, rule = await _the_soxl_book(s, "full_build_probe@example.com")
            out = await rr.build_recommendations(s, user)
            fresh = await s.get(TradingRule, rule.id)
    finally:
        await eng.dispose()
    short = str(rule.id)[:8]
    visible = [r["id"] for r in out["recommendations"] + out["more_recommendations"]]
    assert f"rule_{short}" in visible
    d = next(r for r in out["recommendations"] if r["id"] == f"rule_{short}")
    assert d["apply"]["kind"] == "rule_vs_sleeve"
    assert out["rule_banner"]["carded"] == [short] and out["rule_banner"]["healed"] == []
    assert fresh.triggered is True and fresh.active is True


@pytest.mark.asyncio
async def test_a_cap_blocked_by_its_sleeve_is_neither_counted_nor_retired():
    eng, Session = _session()
    try:
        async with Session() as s:
            user, tp = await _the_soxl_book(s, "blocked_cap_probe@example.com")
            tp.active, tp.triggered = False, False
            cap = await rs.create_rule(s, user, ticker="SOXL", rule_type="max_weight",
                                       mode="pct", level=30.0)
            cap.triggered = True
            s.add(RuleEvent(subject=user.email, rule_id=str(cap.id), ticker="SOXL",
                            rule_type="max_weight", title="cap", trigger_price=151.45,
                            target_price=30.0, outcome="triggered"))
            await s.commit()
            out1 = await rr.build_recommendations(s, user)
            out2 = await rr.build_recommendations(s, user)
            fresh = await s.get(TradingRule, cap.id)
            ev = (await s.scalars(select(RuleEvent).where(
                RuleEvent.rule_id == str(cap.id)))).all()
    finally:
        await eng.dispose()
    short = str(cap.id)[:8]
    for out in (out1, out2):
        assert out["rule_banner"]["healed"] == [], "the app's own suppression retires nothing"
        assert short in out["rule_banner"]["blocked"]
        assert short not in out["rule_banner"]["carded"], "no banner for a card you cannot tap"
        assert any(h["id"] == f"rule_{short}" and h["how"] == "blocked" and h["reason"]
                   for h in out["hidden_cards"]), "listed in the history, with the reason"
    assert fresh.triggered is True and fresh.active is True
    assert [e.outcome for e in ev] == ["triggered"], "no acknowledged stamp, on any load"


@pytest.mark.asyncio
async def test_a_failed_conversion_does_not_retire_the_take_profit(monkeypatch):
    eng, Session = _session()
    try:
        async with Session() as s:
            user, rule = await _the_soxl_book(s, "failed_conversion_probe@example.com")

            async def _transient(*a, **k):
                raise RuntimeError("transient")
            monkeypatch.setattr(sv, "sleeves_holding", _transient)
            out = await rr.build_recommendations(s, user)
            fresh = await s.get(TradingRule, rule.id)
    finally:
        await eng.dispose()
    short = str(rule.id)[:8]
    assert out["rule_banner"]["healed"] == []
    assert short in out["rule_banner"]["blocked"]
    assert fresh.active is True and fresh.triggered is True and float(fresh.level) == 148.52


# --------------------------------------------------------------------------- #
# 7. The sleeve moves by what was actually sold
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_partial_fill_moves_the_sleeve_partially(monkeypatch):
    eng, Session = _session()
    try:
        async with Session() as s:
            user, rule = await _the_soxl_book(s, "partial_fill_probe@example.com")
            recs, _f = await _decision(s, user)
            card = next(r for r in recs if r.get("decision"))
            sell = card["apply"]["options"]["sell"]
            # Between building the card and tapping Sell, the book lost shares:
            # only 10 of the planned 25 can be sold.
            rows = {p.ticker: p for p in await list_positions(s, user)}
            rows["SOXL"].quantity = 10
            await s.commit()
            _serve(monkeypatch, card)
            out = await rr.apply_recommendation(s, user, card["id"], choice="sell")
            sleeves = {x.strategy_id: float(x.sleeve_pct) for x in await sv.list_sleeves(s, user)}
    finally:
        await eng.dispose()
    assert out["shares"] == pytest.approx(10.0)
    frm, to = sell["sleeve_pct_from"], sell["sleeve_pct_to"]
    assert sleeves[SLEEVE] == pytest.approx(round(frm - (frm - to) * 10 / 25, 2))


def test_the_sleeve_cut_is_sized_from_value_not_one_rows_share_count():
    src = inspect.getsource(rr._rule_conflicts_to_decisions)
    assert "sold_pct = gross / float(nav) * 100.0" in src
    assert 'pos.get("qty")' not in src
