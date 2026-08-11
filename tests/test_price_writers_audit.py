"""The audit after the 2026-08-10 cash incident, turned into tests.

That incident: `POST /portfolio/refresh-prices` kept its own copy of the reprice
loop, without the cash guard, and quoted the synthetic CASH row against the real
NASDAQ ticker CASH (Pathward Financial, ~$86). ₪642 of shekels became ~₪167k of
phantom NAV.

The lesson recorded at the time was that a guard protects the code path it is
on, not the behaviour — so the fix is only finished once every writer of
`current_price` has been checked. This file pins the result of that sweep.
"""
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.schemas.intake import IntakePosition
from app.schemas.state_machine import Market
from app.services import strategy_profile as prof
from app.services.feed_service import ensure_user
from app.services.intake_service import (
    ensure_account, ensure_entity, get_cash, list_positions, set_cash,
    update_position, upsert_positions)


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


@pytest.mark.asyncio
async def test_editing_a_holding_cannot_misprice_the_cash_row():
    """The last unguarded writer of current_price.

    Nothing in the UI reaches it — the Holdings cash row opens the cash editor,
    not the holding editor — but the API does, and "no UI path" is not a
    guarantee. Editing the balance must still work; mispricing it must not.
    """
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await ensure_user(s, "cash_edit_probe@example.com")
            entity = await ensure_entity(s, user, "Personal", "Personal")
            account = await ensure_account(s, entity, "Main")
            await upsert_positions(s, account, [IntakePosition(
                ticker="MSFT", market=Market.NASDAQ, depth=2, spot_price=100.0,
                listing_price=100.0, quantity=10, cost_basis=100.0,
                asset_class="Equities")])
            await s.commit()
            await set_cash(s, user, 642.19)

            cash = next(p for p in await list_positions(s, user)
                        if p.ticker.upper() == "CASH")
            # Exactly what corrupted the live book, via the other door.
            await update_position(s, user, str(cash.id),
                                  current_price=86.85, cost_basis=86.85)
            rows = {p.ticker.upper(): p for p in await list_positions(s, user)}
            balance = await get_cash(s, user)
    finally:
        await eng.dispose()

    assert float(rows["CASH"].current_price) == 1.0
    assert float(rows["CASH"].cost_basis) == 1.0
    assert rows["CASH"].meta.get("price_currency") == "ILS"
    assert balance == pytest.approx(642.19), "the balance itself must be untouched"


@pytest.mark.asyncio
async def test_the_cash_balance_can_still_be_edited():
    """The guard must not turn into a lock."""
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await ensure_user(s, "cash_edit_ok@example.com")
            entity = await ensure_entity(s, user, "Personal", "Personal")
            await ensure_account(s, entity, "Main")
            await s.commit()
            await set_cash(s, user, 500.0)
            cash = next(p for p in await list_positions(s, user)
                        if p.ticker.upper() == "CASH")
            await update_position(s, user, str(cash.id), quantity=900.0)
            balance = await get_cash(s, user)
    finally:
        await eng.dispose()

    assert balance == pytest.approx(900.0)


def test_no_ticker_the_app_itself_references_falls_through_to_single_name():
    """`_character` returning "single_name" means 32% assumed volatility, and
    that feeds compute_snapshot's fallback — so an unclassified ETF is risk-
    scored as if it were one company.

    The sweep found two: COW (a commodity ETN, and in the live book) and IWM —
    which P3 had just started using as a regime breadth member, so the app was
    measuring market breadth with an index it believed was a single stock.
    """
    etfs = ["IWM", "COW", "SPY", "QQQ", "VTI", "VXUS", "SCHD", "BND", "BIL",
            "TQQQ", "SOXL", "SMH", "MTUM", "QUAL", "AVUV", "USMV", "DBC",
            "TIP", "HYG", "JEPI", "SHY", "VIG", "VYM", "IAU"]
    unclassified = [t for t in etfs if prof._character(t) == "single_name"]
    assert not unclassified, f"funds modelled as single stocks: {unclassified}"


def test_real_single_names_are_still_single_names():
    """The counter-check: widening the buckets must not swallow actual stocks."""
    for tk in ("MSFT", "AMZN", "NVDA", "META", "V", "TEVA", "GOOGL", "AVGO"):
        assert prof._character(tk) == "single_name", tk
        _r, vol = prof.assumptions_for(tk)
        assert vol >= 30.0, f"{tk} is a single stock and should carry its risk"


def test_the_regime_breadth_set_is_fully_classified():
    """P3 introduced these. An index the risk model does not recognise is one it
    prices as a 32%-volatility company."""
    from app.engines import regime as rg
    for tk in rg.tickers_needed():
        assert prof._character(tk) != "single_name", tk


def test_a_delisted_commodity_etn_is_priced_as_a_commodity():
    """COW sits in the live book. At single_name it carried 32% assumed vol; as a
    commodity it carries ~18%, which lowers the book's weighted volatility and
    therefore RAISES the risk score — the old number was pessimistic by artefact,
    not by measurement."""
    assert prof._character("COW") == "commodity"
    _r, vol = prof.assumptions_for("COW")
    assert 10.0 < vol < 25.0


@pytest.mark.asyncio
async def test_upsert_never_writes_a_price_onto_an_existing_cash_row():
    """`upsert_positions` overwrites price and meta wholesale for a row it
    matches by ticker, so a CASH line would reprice the balance -- the
    2026-08-10 incident through a third door.

    This test previously DOCUMENTED the gap and said "if this assertion ever
    flips, add the guard here". It flipped when the guard went in, which is the
    documentation-test earning its keep: it named the next place to look instead
    of leaving the hole to be rediscovered.
    """
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await ensure_user(s, "cash_upsert_probe@example.com")
            entity = await ensure_entity(s, user, "Personal", "Personal")
            account = await ensure_account(s, entity, "Main")
            await s.commit()
            await set_cash(s, user, 1000.0)
            await upsert_positions(s, account, [IntakePosition(
                ticker="CASH", market=Market.NASDAQ, depth=1, spot_price=86.85,
                listing_price=86.85, quantity=1500, cost_basis=86.85,
                asset_class="Equities")])
            await s.commit()
            after = {p.ticker.upper(): p for p in await list_positions(s, user)}
            balance = await get_cash(s, user)
    finally:
        await eng.dispose()

    assert float(after["CASH"].current_price) == 1.0, "cash stays ILS-native"
    assert float(after["CASH"].cost_basis) == 1.0
    assert after["CASH"].meta.get("price_currency") == "ILS"
    # The quantity IS the balance, so an upsert may still change it.
    assert balance == pytest.approx(1500.0)
