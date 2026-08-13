"""P1 — the sleeve has to mean something.

``as_legacy_strategy`` hardcodes ``{"Equities": 1.0}`` for every strategy in this
family, so ``sleeve_pct`` only ever changed the *basket*: applying at 20% and at
90% wrote an identical plan. Asset-class allocation cannot express the difference
either, because TQQQ and QQQ are both Equities.

* P1.1 — applying arms a ``max_weight`` at the sleeve size, so the number the
  slider shows is continuously enforced.
* P1.2 — "What changes?" shows the funding plan instead of near-empty
  asset-class rebalance actions.
"""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.tables import TradingRule
from app.schemas.intake import IntakePosition
from app.schemas.state_machine import Market
from app.services import strategy_service as ss
from app.services.feed_service import ensure_user
from app.services.intake_service import (
    ensure_account, ensure_entity, set_cash, upsert_positions)

TREND = "btm_trend_tqqq"          # aggressive TQQQ, core QQQ, suggested sleeve 20%


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


async def _book(s, email, positions=(), cash=0.0):
    user = await ensure_user(s, email)
    entity = await ensure_entity(s, user, "Personal", "Personal")
    account = await ensure_account(s, entity, "Main")
    if positions:
        await upsert_positions(s, account, list(positions))
    await s.commit()
    if cash:
        await set_cash(s, user, cash)
    return user


def _pos(ticker, qty=100, price=100.0):
    return IntakePosition(ticker=ticker, market=Market.TASE, depth=2, spot_price=price,
                          listing_price=price, quantity=qty, cost_basis=price,
                          asset_class="Equities")


async def _caps(s, user) -> list[TradingRule]:
    return list((await s.scalars(
        select(TradingRule).where(TradingRule.subject == user.email,
                                  TradingRule.rule_type == "max_weight"))).all())


# --------------------------------------------------------------------------- #
# P1.1
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_applying_arms_a_cap_at_the_sleeve_size():
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "cap_probe@example.com", [_pos("MSFT")])
            res = await ss.apply_strategy(s, user, TREND, sleeve_pct=20)
            caps = await _caps(s, user)
    finally:
        await eng.dispose()

    assert res["ok"]
    assert [c.ticker for c in caps] == ["TQQQ"]      # the core is never capped
    assert float(caps[0].level) == pytest.approx(20.0)
    assert caps[0].mode == "pct" and caps[0].active
    assert res["sleeve_caps"][0]["action"] == "armed"
    # The honest limit of option (a), said out loud on the response.
    assert "does not make the rebalancer aim for it" in res["sleeve_cap_note"]


@pytest.mark.asyncio
async def test_two_sleeve_sizes_produce_two_different_plans():
    """The bug in one test: 20% and 90% used to be indistinguishable."""
    eng, Session = _session()
    try:
        async with Session() as s:
            u20 = await _book(s, "sleeve20@example.com", [_pos("MSFT")])
            r20 = await ss.apply_strategy(s, u20, TREND, sleeve_pct=20)
            u90 = await _book(s, "sleeve90@example.com", [_pos("MSFT")])
            r90 = await ss.apply_strategy(s, u90, TREND, sleeve_pct=90)
            c20 = await _caps(s, u20)
            c90 = await _caps(s, u90)
    finally:
        await eng.dispose()

    assert float(c20[0].level) == pytest.approx(20.0)
    assert float(c90[0].level) == pytest.approx(90.0)
    assert r20["sleeve_caps"] != r90["sleeve_caps"]


@pytest.mark.asyncio
async def test_arming_is_idempotent_and_relevels_instead_of_stacking():
    """Applying twice must not leave two caps on one ticker at two levels."""
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "idem_probe@example.com", [_pos("MSFT")])
            await ss.apply_strategy(s, user, TREND, sleeve_pct=20)
            await ss.apply_strategy(s, user, TREND, sleeve_pct=20)
            same = await _caps(s, user)
            res = await ss.apply_strategy(s, user, TREND, sleeve_pct=45)
            after = await _caps(s, user)
    finally:
        await eng.dispose()

    assert len(same) == 1, "applying twice at the same size must not stack a second cap"
    assert len(after) == 1, "changing the sleeve must re-level, not add"
    assert float(after[0].level) == pytest.approx(45.0)
    assert res["sleeve_caps"][0]["previous_level"] == pytest.approx(20.0)
    assert res["sleeve_caps"][0]["action"] == "relevelled"


@pytest.mark.asyncio
async def test_a_zero_sleeve_arms_nothing():
    """A 0% cap is breached by any holding at all, so it would fire the instant
    it was armed -- the app springing a sale rather than setting a guard.

    C2 turned this from a silent no-op into a refusal. Applying at 0% used to
    write the plan and arm nothing, leaving a strategy "applied" that governed no
    part of the book. There is now a way to say that properly -- remove the
    sleeve -- so 0% gets an answer instead of a shrug. The original protection is
    unchanged and still asserted: no 0% cap is ever armed.
    """
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "zero_probe@example.com", [_pos("MSFT")])
            res = await ss.apply_strategy(s, user, TREND, sleeve_pct=0)
            caps = await _caps(s, user)
            # A refusal must not expire the caller's ORM objects. An earlier
            # draft called session.rollback() here, which expires everything in
            # the session -- reading user.email inside _caps above is what
            # caught it, with the same MissingGreenlet-adjacent failure
            # CLAUDE.md already warns about.
            assert user.email == "zero_probe@example.com"
    finally:
        await eng.dispose()

    assert caps == [], "no cap may be armed at 0%"
    assert res["ok"] is False
    assert "removal" in res["reason"], res["reason"]


@pytest.mark.asyncio
async def test_a_static_family_is_a_portfolio_not_a_sleeve():
    """Capping every leg of a model basket would be nonsense."""
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "static_probe@example.com", [_pos("MSFT")])
            res = await ss.apply_strategy(s, user, "grow_quality")
            caps = await _caps(s, user)
    finally:
        await eng.dispose()

    assert caps == []
    assert res.get("sleeve_caps") == []


# --------------------------------------------------------------------------- #
# P1.2
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_preview_shows_the_funding_plan_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(ss, "guarded_quote",
                        lambda tk: NS(price=100.0, currency="ILS", market="TASE"))
    from app.api.routes import strategy as route
    from app.services.allocation_mix import current_mix
    from app.services.intake_service import list_positions
    from app.services.plan_service import get_plan

    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "preview_probe@example.com", [_pos("MSFT", 100, 100.0)],
                               cash=5000.0)
            rows = await list_positions(s, user)
            mix, nav = current_mix(rows)
            legacy = route.strategy_catalog.as_legacy_strategy(TREND, 20)
            out = await route.apply_strategy_preview(
                s, user, legacy, await get_plan(s, user), mix, nav,
                strategy_id=TREND, sleeve_pct=20)
            tickers = {p.ticker for p in await list_positions(s, user)}
            caps = await _caps(s, user)
    finally:
        await eng.dispose()

    # The direct answer to "what do I need to get rid of?"
    assert out["funding"] is not None
    assert out["funding"]["buys"], "the preview must name what it would buy"
    assert out["funding"]["dry_run"] is True
    # And what applying would arm.
    assert out["sleeve_cap"] == [{"ticker": "TQQQ", "level_pct": 20.0}]
    # Read-only, both halves: no purchase, and no rule armed by looking.
    assert "TQQQ" not in tickers
    assert caps == []


@pytest.mark.asyncio
async def test_the_preview_differs_between_sleeve_sizes(monkeypatch):
    monkeypatch.setattr(ss, "guarded_quote",
                        lambda tk: NS(price=100.0, currency="ILS", market="TASE"))
    from app.api.routes import strategy as route
    from app.services.allocation_mix import current_mix
    from app.services.intake_service import list_positions
    from app.services.plan_service import get_plan

    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "preview_diff@example.com", [_pos("MSFT", 100, 100.0)],
                               cash=5000.0)
            rows = await list_positions(s, user)
            mix, nav = current_mix(rows)
            plan = await get_plan(s, user)
            small = await route.apply_strategy_preview(
                s, user, route.strategy_catalog.as_legacy_strategy(TREND, 10),
                plan, mix, nav, strategy_id=TREND, sleeve_pct=10)
            big = await route.apply_strategy_preview(
                s, user, route.strategy_catalog.as_legacy_strategy(TREND, 30),
                plan, mix, nav, strategy_id=TREND, sleeve_pct=30)
    finally:
        await eng.dispose()

    assert small["sleeve_cap"] != big["sleeve_cap"]
    small_buy = small["funding"]["buys"][0]["buy_ils"]
    big_buy = big["funding"]["buys"][0]["buy_ils"]
    assert big_buy > small_buy, "a bigger sleeve must cost more to fund"
