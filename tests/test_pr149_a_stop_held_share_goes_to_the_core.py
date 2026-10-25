"""PR 149 -- money a stop-held sleeve cannot use goes to the core, not to cash (D127).

Read live from production on 2026-10-25 08:15 (Israel):

* `/recommendations.book_target`: `held_back = {"SOXL": 0.23}`, target Cash 0.2376
  with a deliberate reserve of only 0.0076. Cash held: ₪4,699 of ₪19,778 (23.8%).
* The SOXL sleeve (23%) is on a STOP hold since the 10-23 05:45 stop-out.

D127 (owner, 2026-10-23, "Into the core") says that share goes to the core and
claims `book_target` already did that. It did not: `compose` parks every hold's
cut in Cash. That is PR 75's design for a TAKE-PROFIT hold (the cash waits to buy
the same ticker back at a lower price); a stop hold has no price that ends it, so
the cash waited on a signal with no date, and the target kept it there because
PR 86 caps the reserve at the cash that exists -- the cash existed because the
reserve kept it.

The rule now: a stop hold still cuts the ticker's target (no buy), and its share
flows to the core. A take-profit hold keeps parking its share in Cash.
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
from app.services import sleeve_service as sv  # noqa: E402
from app.services.book_target import book_target, compose  # noqa: E402
from app.services.feed_service import ensure_user  # noqa: E402
from app.services.intake_service import (  # noqa: E402
    ensure_account, ensure_entity, set_cash, upsert_positions)
from app.services.plan_service import upsert_plan  # noqa: E402

SOXL = "btm_trend_soxl"


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


def _pos(tk, qty, cls):
    return IntakePosition(ticker=tk, market=Market.TASE, depth=2, spot_price=100.0,
                          listing_price=100.0, quantity=qty, cost_basis=90.0, asset_class=cls)


async def _book(s, email):
    """The live shape: SOXL sold out (no row), its proceeds in cash, a 23% sleeve."""
    user = await ensure_user(s, email)
    ent = await ensure_entity(s, user, "Personal", "Personal")
    acc = await ensure_account(s, ent, "Main")
    await upsert_positions(s, acc, [_pos("VTI", 120, "Equities"), _pos("BND", 30, "Fixed Income")])
    await s.commit()
    await set_cash(s, user, 4000.0)          # 4,000 of a 19,000 book: ~21%
    await upsert_plan(s, user, objective="Grow", risk_tolerance="High", horizon_years=6,
                      target_amount=100000, cash_target_ils=150, cash_redeploy_at_ils=1500)
    res = await sv.add_or_resize(s, user, SOXL, 23.0)
    assert res["ok"], res
    await s.commit()
    return user


@pytest.mark.asyncio
async def test_a_stop_hold_gives_its_share_to_the_core(ils_quotes):
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "pr149_stop@example.com")
            before = await book_target(s, user, "Grow")
            await bh.record_stop(s, user, ticker="SOXL", sold_price=82.11, shares=15.78,
                                 exit_rule_type="stop_loss", rule_id="rule_c89d8251")
            await s.commit()
            bt = await book_target(s, user, "Grow")
            assert not bt["held_back"].get("SOXL"), (
                "a stop hold must not park its sleeve's share in Cash (D127)", bt["held_back"])
            # the deliberate reserve is all the Cash the target asks for
            assert bt["target"].get("Cash", 0.0) == pytest.approx(bt["cash_pct"], abs=1e-3), bt
            # the core took the 23 points the held sleeve cannot use
            assert bt["core_pct"] == pytest.approx(before["core_pct"] + 0.23, abs=0.01), (before, bt)
            # the plan still shows the sleeve, and no buyer may buy SOXL
            assert bt["planned_sleeve_targets"].get("SOXL") == pytest.approx(0.23, abs=1e-3)
            assert "SOXL" in bh.stop_held(await bh.load(s, user))
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_a_take_profit_hold_still_keeps_its_cash(ils_quotes):
    """NOT CHANGED: PR 75's hold waits for a lower price to buy the same ticker
    back, so its share stays parked in Cash (capped at the cash that exists)."""
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "pr149_tp@example.com")
            await bh.record(s, user, ticker="SOXL", sold_price=90.0, shares=5, strategy_id=SOXL)
            await s.commit()
            bt = await book_target(s, user, "Grow")
            assert bt["held_back"].get("SOXL", 0.0) > 0.15, bt["held_back"]
            assert bt["target"].get("Cash", 0.0) > bt["cash_pct"] + 0.15, bt
    finally:
        await eng.dispose()


def test_compose_is_unchanged_for_what_it_is_given():
    """The pure half still parks what it is handed: the choice of WHICH holds park
    cash is made by the caller, so every existing compose() test keeps its meaning."""
    out = compose(sleeve_targets={"VTI": 0.0}, core_mix={"Equities": 1.0}, objective="Grow",
                  held_back={"SOXL": 0.2})
    assert out["target"]["Cash"] == pytest.approx(0.2, abs=1e-3)


@pytest.fixture
def ils_quotes(monkeypatch):
    from types import SimpleNamespace as NS

    from app.providers import registry
    from app.services import pricing_service, strategy_service

    def _q(tk):
        return NS(ticker=(tk or "").upper(), price=100.0, currency="ILS", market="TASE")
    for mod in (registry, pricing_service, strategy_service):
        monkeypatch.setattr(mod, "guarded_quote", _q, raising=False)
