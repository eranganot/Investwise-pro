"""PR 73 -- a rebalance is trades: every share it moves is a recorded Buy or Sell.

Eran, 2026-09-28, item 2: "what is the difference between my position to the
ticker price?" They are different charts; they looked identical because the
position chart could not rebuild QQQ's share count from the ledger and fell
back to the price. The cause, PROVEN (plan section 1.2, STATUS PR 73):

  * `_rebalance_to` computed one factor per asset class and WROTE the new share
    counts into the book -- no sale, no purchase, no tax, no ledger row;
  * reproduced on a book shaped like his: Equities x1.584, Fixed Income
    x0.2475, Commodities x1.1, zero transactions;
  * matched live: the 2026-09-26 13:14:49 rebalance scaled USO and UGA by
    1.0295 and SOXL and VTI by 1.00268 (two holdings per class, five decimals),
    and QQQ carries that one plus the 09-20 14:17 one.

Eran's decision (Q3): a rebalance's sales owe tax like any other sale.
"""
from __future__ import annotations

import logging

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.tables import Transaction
from app.schemas.intake import IntakePosition
from app.schemas.state_machine import Market
from app.services import recommendations as rr
from app.services.feed_service import ensure_user
from app.services.fx import book_nav_ils
from app.services.intake_service import (
    ensure_account, ensure_entity, list_positions, upsert_positions)


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


def _pos(tk, qty, px, basis, cls):
    return IntakePosition(ticker=tk, market=Market.TASE, depth=2, spot_price=px,
                          listing_price=px, quantity=qty, cost_basis=basis, asset_class=cls)


async def _book(s, email, gains=True):
    """Equities under a Grow target, Fixed Income far over, Commodities a little under.

    With `gains`, BND was bought at 80 and trades at 100, so selling it owes tax.
    """
    user = await ensure_user(s, email)
    ent = await ensure_entity(s, user, "Personal", "Personal")
    acc = await ensure_account(s, ent, "Main")
    b = 80.0 if gains else 100.0
    await upsert_positions(s, acc, [
        _pos("QQQ", 20.0, 100.0, 100.0, "Equities"), _pos("VTI", 30.0, 100.0, 100.0, "Equities"),
        _pos("BND", 40.0, 100.0, b, "Fixed Income"),
        _pos("USO", 5.0, 100.0, 100.0, "Commodities"), _pos("UGA", 4.0, 100.0, 100.0, "Commodities")])
    await s.commit()
    return user


def _signed(t):
    q = float(t.quantity)
    return abs(q) if t.txn_type == "Buy" else -abs(q) if t.txn_type == "Sell" else q


@pytest.mark.asyncio
async def test_every_share_a_rebalance_moves_is_a_recorded_trade():
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "rebal_trades@example.com", gains=False)
            before = {p.ticker: float(p.quantity) for p in await list_positions(s, user)}
            detail = await rr._rebalance_to(s, user, await list_positions(s, user), "Grow")
            await rr._record_apply(s, user, "rebalance_to_objective", "rec_test01", detail)
            after = {p.ticker: float(p.quantity) for p in await list_positions(s, user)}
            txns = (await s.scalars(select(Transaction))).all()
    finally:
        await eng.dispose()
    moved = {tk for tk in before if abs(after.get(tk, 0.0) - before[tk]) > 1e-6}
    assert moved == {"QQQ", "VTI", "BND", "USO", "UGA"}, "fixture sanity: everything moves"
    for tk in moved:
        ledger = sum(_signed(t) for t in txns if t.ticker == tk)
        assert ledger == pytest.approx(after.get(tk, 0.0) - before[tk], abs=1e-4), tk
    assert {t.txn_type for t in txns if t.ticker == "BND"} == {"Sell"}
    assert {t.txn_type for t in txns if t.ticker in ("QQQ", "VTI")} == {"Buy"}
    assert all(float(t.price or 0) > 0 for t in txns), "every leg carries its price"


@pytest.mark.asyncio
async def test_without_gains_it_reaches_the_same_mix_the_old_executor_did():
    """No tax owed -> the buys are not scaled, and the per-class factors are the
    ones the pre-PR 73 executor wrote (Equities x1.584, FI x0.2475, Commodities x1.1)."""
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "rebal_same_mix@example.com", gains=False)
            before = {p.ticker: float(p.quantity) for p in await list_positions(s, user)}
            detail = await rr._rebalance_to(s, user, await list_positions(s, user), "Grow")
            after = {p.ticker: float(p.quantity) for p in await list_positions(s, user)}
    finally:
        await eng.dispose()
    f = {tk: after[tk] / before[tk] for tk in before}
    assert f["QQQ"] == pytest.approx(1.584, abs=1e-3) and f["VTI"] == pytest.approx(f["QQQ"], abs=1e-6)
    assert f["BND"] == pytest.approx(0.2475, abs=1e-3)
    assert f["USO"] == pytest.approx(1.1, abs=1e-3) and f["UGA"] == pytest.approx(f["USO"], abs=1e-6)
    assert detail["tax_ils"] == 0.0 and detail["rebalance"]["buy_scale"] == 1.0


@pytest.mark.asyncio
async def test_a_rebalance_with_gains_pays_the_tax_and_the_book_falls_by_exactly_that():
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "rebal_tax@example.com", gains=True)
            rows = await list_positions(s, user)
            nav_before = book_nav_ils(rows)
            plan = rr._rebalance_plan(rows, (await rr._book_target(s, user, "Grow"))["target"])
            detail = await rr._rebalance_to(s, user, rows, "Grow")
            nav_after = book_nav_ils(await list_positions(s, user))
    finally:
        await eng.dispose()
    assert detail["tax_ils"] > 0, "BND was bought at 80 and sells at 100"
    assert detail["tax_ils"] == pytest.approx(plan["tax_ils"]), "the card's number is Accept's"
    assert nav_after == pytest.approx(nav_before - detail["tax_ils"], abs=0.02)
    assert detail["rebalance"]["buy_scale"] < 1.0, "buys shrink to what the sales raise"
    assert detail["rebalance"]["bought_ils"] == pytest.approx(
        detail["rebalance"]["net_proceeds_ils"], abs=0.02)
    sold = next(x for x in detail["sold"] if x["ticker"] == "BND")
    assert sold["tax_ils"] > 0 and sold["value_ils"] > 0


def test_the_card_and_accept_share_one_sizing():
    import inspect
    card = inspect.getsource(rr.build_recommendations)
    exe = inspect.getsource(rr._rebalance_to)
    assert "_rebalance_plan(rows, target)" in card
    assert "_rebalance_plan(rows, target)" in exe


# --------------------------------------------------------------------------- #
# The guard: a share count may not change on Accept without a ledger row
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_silent_share_change_on_accept_becomes_an_adjust_and_an_error(
        monkeypatch, caplog):
    """Simulate the next silent writer: a handler that moves shares and reports
    nothing. The accept boundary must record it and say so."""
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "guard_probe@example.com", gains=False)

            async def _silent(session, user_, spec):
                rows = {p.ticker: p for p in await list_positions(session, user_)}
                await rr.update_position(session, user_, str(rows["QQQ"].id), quantity=21.5)
                return {"note": "moved QQQ and said nothing"}
            monkeypatch.setattr(rr, "_apply_redeploy_cash", _silent)

            card = {"id": "rec_silent", "title": "silent", "severity": "LOW",
                    "apply": {"kind": "redeploy_cash", "legs": []}}

            async def _fake_build(session, user_):
                return {"recommendations": [card], "more_recommendations": []}
            monkeypatch.setattr(rr, "build_recommendations", _fake_build)
            with caplog.at_level(logging.ERROR, logger="app.services.recommendations"):
                out = await rr.apply_recommendation(s, user, "rec_silent")
            adj = (await s.scalars(select(Transaction).where(
                Transaction.txn_type == "Adjust"))).all()
    finally:
        await eng.dispose()
    assert out["unrecorded"] == [{"ticker": "QQQ", "shares": 1.5}]
    assert [(a.ticker, float(a.quantity)) for a in adj] == [("QQQ", 1.5)]
    assert "UNRECORDED SHARE CHANGE" in caplog.text


@pytest.mark.asyncio
async def test_an_honest_accept_writes_no_adjust(monkeypatch):
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "guard_honest@example.com", gains=True)
            card = {"id": "rec_rebal1", "title": "Buy Equities", "severity": "LOW",
                    "apply": {"kind": "rebalance_to_objective", "asset_class": "Equities"}}

            async def _fake_build(session, user_):
                return {"recommendations": [card], "more_recommendations": []}
            monkeypatch.setattr(rr, "build_recommendations", _fake_build)
            out = await rr.apply_recommendation(s, user, "rec_rebal1")
            adj = (await s.scalars(select(Transaction).where(
                Transaction.txn_type == "Adjust"))).all()
            legs = (await s.scalars(select(Transaction))).all()
    finally:
        await eng.dispose()
    assert out["unrecorded"] == [] and adj == []
    assert out["tax_ils"] > 0 and len(legs) == 5, "one leg per holding moved"


def test_the_position_tab_never_shows_the_price_as_your_position():
    """B4: with no position line, the header says so and labels the price."""
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "app" / "static_app" / "index.html"
            ).read_text(encoding="utf-8")
    body = html[html.index("function renderHist("):html.index("function renderBuys(")]
    assert "Can't draw your position yet." in body
    assert "price (not your position)" in body
    assert 'const cant=HIST.mode==="value"&&!v.available;' in body


# --------------------------------------------------------------------------- #
# Found in independent review of this PR, reproduced, fixed
# --------------------------------------------------------------------------- #
async def _two_row_book(s, email):
    """BND held in TWO accounts -- the shape that broke the first version."""
    user = await ensure_user(s, email)
    ent = await ensure_entity(s, user, "Personal", "Personal")
    acc = await ensure_account(s, ent, "Main")
    await upsert_positions(s, acc, [_pos("SCHD", 110.0, 100.0, 90.0, "Equities"),
                                    _pos("BND", 90.0, 100.0, 90.0, "Fixed Income")])
    ent2 = await ensure_entity(s, user, "Trust", "Trust")
    acc2 = await ensure_account(s, ent2, "Main")
    await upsert_positions(s, acc2, [_pos("BND", 40.0, 100.0, 90.0, "Fixed Income")])
    await s.commit()
    return user


@pytest.mark.asyncio
async def test_a_ticker_in_two_rows_is_rebalanced_row_by_row_and_never_wiped():
    """Review, CONFIRMED: keyed by ticker, a 71.5-share leg was sold out of the
    40-share row, went negative, and delete_position removed EVERY BND row --
    about 48k ILS gone, committed before the NAV check could object."""
    eng, S = _session()
    try:
        async with S() as s:
            user = await _two_row_book(s, "two_rows_rebal@example.com")
            rows = await list_positions(s, user)
            nav_before = book_nav_ils(rows)
            detail = await rr._rebalance_to(s, user, rows, "Grow")
            await rr._record_apply(s, user, "rebalance_to_objective", "rec_tworow", detail)
            after = await list_positions(s, user)
            txns = (await s.scalars(select(Transaction))).all()
    finally:
        await eng.dispose()
    bnd_after = sum(float(p.quantity) for p in after if p.ticker == "BND")
    bnd_sold = sum(float(t.quantity) for t in txns if t.ticker == "BND" and t.txn_type == "Sell")
    assert bnd_after > 0, "the holding survives"
    assert bnd_after == pytest.approx(130.0 - bnd_sold, abs=1e-4), "every share sold is recorded"
    assert book_nav_ils(after) == pytest.approx(nav_before - detail["tax_ils"], abs=0.05)


@pytest.mark.asyncio
async def test_the_guard_sees_a_two_row_ticker_as_one_and_stays_quiet(monkeypatch):
    """Review, CONFIRMED: the guard's 'before' kept one row per ticker, so every
    accept on a two-row ticker wrote a phantom Adjust."""
    eng, S = _session()
    try:
        async with S() as s:
            user = await _two_row_book(s, "two_rows_guard@example.com")
            card = {"id": "rec_tworow", "title": "Rebalance", "severity": "LOW",
                    "apply": {"kind": "rebalance_to_objective"}}

            async def _fake_build(session, user_):
                return {"recommendations": [card], "more_recommendations": []}
            monkeypatch.setattr(rr, "build_recommendations", _fake_build)
            out = await rr.apply_recommendation(s, user, "rec_tworow")
            adj = (await s.scalars(select(Transaction).where(
                Transaction.txn_type == "Adjust"))).all()
    finally:
        await eng.dispose()
    assert out["unrecorded"] == [] and adj == []


@pytest.mark.asyncio
async def test_sell_losers_records_the_shares_it_sold(monkeypatch):
    """Review, CONFIRMED (pre-existing): sell_losers reported a bare ticker, so
    the ledger wrote a 0-share Sell and the guard had to write the Adjust."""
    eng, S = _session()
    try:
        async with S() as s:
            user = await ensure_user(s, "sell_losers_probe@example.com")
            ent = await ensure_entity(s, user, "Personal", "Personal")
            acc = await ensure_account(s, ent, "Main")
            await upsert_positions(s, acc, [_pos("TQQQ", 19.1041, 50.0, 70.0, "Equities"),
                                            _pos("VTI", 10.0, 100.0, 100.0, "Equities")])
            await s.commit()
            card = {"id": "rec_losers", "title": "Harvest a tax loss", "severity": "LOW",
                    "apply": {"kind": "sell_losers", "tickers": ["TQQQ"]}}

            async def _fake_build(session, user_):
                return {"recommendations": [card], "more_recommendations": []}
            monkeypatch.setattr(rr, "build_recommendations", _fake_build)
            out = await rr.apply_recommendation(s, user, "rec_losers")
            legs = (await s.scalars(select(Transaction).where(
                Transaction.ticker == "TQQQ"))).all()
    finally:
        await eng.dispose()
    assert out["unrecorded"] == []
    assert [(t.txn_type, float(t.quantity)) for t in legs] == [("Sell", pytest.approx(19.1041))]
