"""PR 75 -- selling a take-profit slice keeps the sleeve's target; nothing buys it back
while the price stays above the sale price.

Eran, 2026-09-28: "if I chose to sell it should not change the goal of the
sleeve only the current holding of it (target should remain the same as
planned)" -- and, asked what stops the next card re-buying it, "Hold until
price retreats".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.tables import StrategySignalState
from app.services import buyback_hold as bh
from app.services import recommendations as rr
from app.services import sleeve_service as sv
from app.services.book_target import book_target, compose
from app.services.intake_service import list_positions
from tests.test_a_rule_that_fights_a_sleeve_is_a_decision import (
    SLEEVE, _decision, _serve, _session, _the_soxl_book)


class _Row:
    def __init__(self, tk, px):
        self.ticker, self.current_price = tk, px


def test_a_hold_is_active_until_the_price_falls_below_the_sale():
    holds = {"SOXL": {"level": 151.45}}
    assert "SOXL" in bh.active(holds, [_Row("SOXL", 160.0)])
    assert "SOXL" in bh.active(holds, [_Row("SOXL", 151.45)]), "the sale price is not a retreat"
    assert "SOXL" not in bh.active(holds, [_Row("SOXL", 140.0)])
    assert "SOXL" in bh.active(holds, []), "no price is not a retreat"


def test_apply_holds_meets_the_target_at_the_current_weight_and_never_raises_it():
    eff, held = bh.apply_holds({"SOXL": 0.40, "QQQ": 0.09}, {"SOXL": {"level": 1}},
                               {"SOXL": 0.30, "QQQ": 0.05})
    assert eff == {"SOXL": pytest.approx(0.30), "QQQ": 0.09}
    assert held == {"SOXL": pytest.approx(0.10)}
    eff2, held2 = bh.apply_holds({"SOXL": 0.40}, {"SOXL": {"level": 1}}, {"SOXL": 0.45})
    assert eff2 == {"SOXL": 0.40} and held2 == {}, "over target is not a hold's business"


def test_the_held_back_weight_waits_in_cash_not_in_the_core():
    base = compose(sleeve_targets={"SOXL": 0.40}, core_mix=None, objective="Grow")
    held = compose(sleeve_targets={"SOXL": 0.30}, core_mix=None, objective="Grow",
                   held_back={"SOXL": 0.10})
    assert held["target"]["Cash"] == pytest.approx(base["target"].get("Cash", 0.0) + 0.10)
    for cls in ("Fixed Income", "Commodities"):
        assert held["target"].get(cls, 0) == pytest.approx(base["target"].get(cls, 0)), cls
    assert held["target"]["Equities"] == pytest.approx(base["target"]["Equities"] - 0.10)
    assert sum(held["target"].values()) == pytest.approx(1.0, abs=1e-3)


def _buys_soxl(out: dict) -> list[str]:
    hits = []
    for r in out.get("recommendations", []) + out.get("more_recommendations", []):
        ap = r.get("apply") or {}
        if ap.get("kind") in ("trim", "sell", "sell_losers", "rule_vs_sleeve", "none"):
            continue
        if ap.get("kind") in ("fund_sleeve", "fund_plan") and (
                str((r.get("meta") or {}).get("ticker") or "").upper() == "SOXL"
                or ap.get("strategy_id") == SLEEVE):
            hits.append(r["title"])
            continue
        if (ap.get("ticker") or "").upper() == "SOXL" or any(
                (x.get("ticker") or "").upper() == "SOXL" for x in ap.get("legs") or []):
            hits.append(r["title"])
    return hits


@pytest.mark.asyncio
async def test_after_the_sale_nothing_buys_it_back_until_the_price_retreats(monkeypatch):
    eng, Session = _session()
    try:
        async with Session() as s:
            user, _rule = await _the_soxl_book(s, "holdback_probe@example.com")
            # Eran's own cash settings, so the cash card IS built after the sale
            # (review: without a trigger the test never saw that card, and that
            # card was the one buying SOXL straight back).
            from app.services.plan_service import upsert_plan
            await upsert_plan(s, user, objective="Grow", risk_tolerance="High",
                              cash_target_ils=150, cash_redeploy_at_ils=1500)
            await s.commit()
            recs, _f = await _decision(s, user)
            card = next(r for r in recs if r.get("decision"))
            _serve(monkeypatch, card)
            out = await rr.apply_recommendation(s, user, card["id"], choice="sell")
            monkeypatch.undo()
            assert out["buyback_hold"]["level"] == pytest.approx(151.45)

            sleeves = {x.strategy_id: float(x.sleeve_pct) for x in await sv.list_sleeves(s, user)}
            bt = await book_target(s, user, "Grow")
            held_build = await rr.build_recommendations(s, user)

            # CONTROL: the same book with the hold removed does ask to buy SOXL
            # back -- so the assertion above is measuring the hold, not luck.
            saved = await bh.load(s, user)
            await bh._save(s, user, {})
            await s.commit()
            control = await rr.build_recommendations(s, user)
            await bh._save(s, user, saved)
            await s.commit()

            # The price falls back to the sale price: the hold ends for good.
            for p in await list_positions(s, user):
                if p.ticker == "SOXL":
                    p.current_price = 151.0
            await s.commit()
            await rr.build_recommendations(s, user)
        # A FRESH session: the release must be COMMITTED (review, CONFIRMED:
        # the build never committed, so a released hold came back).
        async with Session() as s2:
            after_retreat = await bh.load(s2, user)
    finally:
        await eng.dispose()
    assert sleeves[SLEEVE] == pytest.approx(40.0)
    assert bt["planned_sleeve_targets"]["SOXL"] == pytest.approx(0.40)
    assert bt["held_back"].get("SOXL", 0) > 0.05
    assert _buys_soxl(control), "control: without the hold the book is re-bought"
    assert _buys_soxl(held_build) == [], _buys_soxl(held_build)
    cash_cards = [r for r in held_build["recommendations"] + held_build.get("more_recommendations", [])
                  if (r.get("apply") or {}).get("kind") == "redeploy_cash"]
    for c in cash_cards:
        assert any("stays in cash for SOXL" in h for h in c["how"]), c["how"]
    assert after_retreat == {}, "a retreat releases the hold"


@pytest.mark.asyncio
async def test_the_recommended_answer_follows_the_sleeve_signal():
    eng, Session = _session()
    try:
        async with Session() as s:
            user, _rule = await _the_soxl_book(s, "signal_default_probe@example.com")
            today = datetime.now(timezone.utc).date().isoformat()
            s.add(StrategySignalState(subject=user.email, strategy_id=SLEEVE,
                                      target={"SOXL": 1.0}, as_of=today))
            await s.commit()
            in_ = await rr._decision_recommendation(s, user, SLEEVE, "SOXL")
            row = await s.get(StrategySignalState, (await s.execute(
                StrategySignalState.__table__.select().where(
                    StrategySignalState.subject == user.email))).first().id)
            row.target = {}
            await s.commit()
            out_ = await rr._decision_recommendation(s, user, SLEEVE, "SOXL")
            row.target = {"SOXL": 1.0}
            row.as_of = (datetime.now(timezone.utc).date() - timedelta(days=10)).isoformat()
            await s.commit()
            stale = await rr._decision_recommendation(s, user, SLEEVE, "SOXL")
    finally:
        await eng.dispose()
    assert in_[0] == "keep" and "still holds SOXL" in in_[1]
    assert out_[0] == "sell" and "is out of SOXL" in out_[1]
    assert stale[0] == "sell" and "too old" in stale[1]


@pytest.mark.asyncio
async def test_a_top_up_accepted_from_today_skips_the_held_ticker():
    """Review, by trace: Accept on a sleeve top-up card loads the WHOLE basket,
    so it would buy the held ticker back. From Today it honours the hold; the
    Plan screen (the user's own buy) does not."""
    eng, Session = _session()
    try:
        async with Session() as s:
            user, _rule = await _the_soxl_book(s, "basket_hold_probe@example.com")
            for p in await list_positions(s, user):
                if p.ticker == "SOXL":
                    p.quantity = 60          # under its 40% target
            await s.commit()
            from app.services.strategy_service import load_basket
            await bh.record(s, user, ticker="SOXL", sold_price=151.45, shares=40,
                            strategy_id=SLEEVE)
            await s.commit()
            today = await load_basket(s, user, SLEEVE, mode="fund", dry_run=True,
                                      honour_holds=True)
            plan_screen = await load_basket(s, user, SLEEVE, mode="fund", dry_run=True)
    finally:
        await eng.dispose()
    assert today["ok"] is False and "buy-back hold" in today["error"]
    assert "SOXL" in str(plan_screen), "the user's own buy is not blocked"


def test_the_book_shows_the_planned_sleeve_while_managing_to_the_hold():
    held = compose(sleeve_targets={"SOXL": 0.30}, core_mix=None, objective="Grow",
                   held_back={"SOXL": 0.10})
    assert held["held_back"] == {"SOXL": 0.1}
