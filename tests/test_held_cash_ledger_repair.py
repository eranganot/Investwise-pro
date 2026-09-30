"""PR 86 -- write the Buy rows a PR 83 held-cash accept left out of the ledger.

Production 2026-09-30: VTI 620, USO 270, BND 270 bought from the SOXL reserve;
cash 1,863.49 -> 703.49; zero ledger rows. Eran: "add a repair that fix those
missing entries". Shares = amount / (price at the accept x FX) -- the accept's
own arithmetic -- dated at the accept (the card's completion mark).
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.models.tables import KVSetting, Transaction  # noqa: E402
from app.schemas.intake import IntakePosition  # noqa: E402
from app.schemas.state_machine import Market  # noqa: E402
from app.services import ledger_repair as lr  # noqa: E402
from app.services.feed_service import ensure_user  # noqa: E402
from app.services.intake_service import ensure_account, ensure_entity, upsert_positions  # noqa: E402

AT = "2026-09-29T21:12:00+00:00"
CARD = "rec_561e88"


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


async def _book(s, email):
    user = await ensure_user(s, email)
    acc = await ensure_account(s, await ensure_entity(s, user, "Personal", "Personal"), "Main")
    # the live quantities AFTER the unrecorded buys
    await upsert_positions(s, acc, [
        IntakePosition(ticker="BND", market=Market.NYSE, depth=1, spot_price=70.23,
                       listing_price=70.23, quantity=15.1303, cost_basis=70.2758,
                       asset_class="Fixed Income"),
        IntakePosition(ticker="VTI", market=Market.NYSE, depth=1, spot_price=375.26,
                       listing_price=375.26, quantity=4.2114, cost_basis=378.5, asset_class="Equities")])
    s.add(KVSetting(key=f"completed_recs:{user.email}",
                    value=json.dumps({CARD: {"at": AT, "amount": 1713.49}})))
    await s.commit()
    return user


@pytest.fixture
def close_prices(monkeypatch):
    px = {"BND": 70.23, "VTI": 375.26}
    monkeypatch.setattr(lr, "_price_at", lambda tk, when: (px.get(tk), "30-min bar"))
    from app.services import fx
    monkeypatch.setattr(fx, "fx_rate", lambda ccy: 3.0561 if ccy == "USD" else 1.0)


@pytest.mark.asyncio
async def test_preview_sizes_each_buy_from_the_accepts_own_arithmetic(close_prices):
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "repair1@example.com")
            p = await lr.held_cash_preview(s, user, card=CARD, legs=lr.parse_legs("BND:270,VTI:620"))
            got = {x["ticker"]: x for x in p["legs"]}
            # the production check: 15.1303 - 1.2580 = 13.8723, the 07:42 basket buy
            assert got["BND"]["shares"] == pytest.approx(1.2580, abs=1e-4)
            assert got["BND"]["held_before"] == pytest.approx(13.8723, abs=1e-4)
            assert got["VTI"]["shares"] == pytest.approx(0.5406, abs=1e-4)
            assert p["at"].startswith("2026-09-29T21:12") and p["to_write"] == 2
            # read-only
            n = (await s.execute(select(Transaction))).scalars().all()
            assert n == []
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_apply_writes_dated_rows_once_and_refuses_without_confirm(close_prices):
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "repair2@example.com")
            legs = lr.parse_legs("BND:270,VTI:620")
            assert (await lr.held_cash_apply(s, user, card=CARD, legs=legs))["ok"] is False
            out = await lr.held_cash_apply(s, user, card=CARD, legs=legs, confirm=True)
            assert sorted(w["ticker"] for w in out["written"]) == ["BND", "VTI"]
            rows = (await s.execute(select(Transaction).where(
                Transaction.subject == user.email))).scalars().all()
            assert len(rows) == 2 and all(r.txn_type == "Buy" for r in rows)
            assert all(r.source == "held_cash_redeploy" for r in rows)
            assert all(str(r.executed_at).startswith("2026-09-29 21:12") for r in rows)
            assert {r.ticker: float(r.value_ils) for r in rows} == {"BND": 270.0, "VTI": 620.0}
            # idempotent
            again = await lr.held_cash_apply(s, user, card=CARD, legs=legs, confirm=True)
            assert again["written"] == []
            assert {x["status"] for x in again["legs"]} == {"already recorded"}
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_it_refuses_what_it_cannot_prove(close_prices):
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "repair3@example.com")
            p = await lr.held_cash_preview(s, user, card="rec_nope", legs=lr.parse_legs("BND:270"))
            assert p["ok"] is False and "no accept time" in p["reason"]
            p = await lr.held_cash_preview(s, user, card=CARD,
                                           legs=lr.parse_legs("USO:270,BND:99999"))
            st = {x["ticker"]: x["status"] for x in p["legs"]}
            assert st["USO"].startswith("not held")
            assert "more than" in st["BND"]
            assert p["to_write"] == 0
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_the_positions_own_quote_wins_when_it_predates_the_accept(close_prices, monkeypatch):
    """The accept bought at the quote stamped on the position (20:00 close); a
    30-minute bar can differ by a cent. Use the exact one while it still stands."""
    from app.services.intake_service import list_positions
    monkeypatch.setattr(lr, "_price_at", lambda tk, when: (70.20, "30-min bar"))
    eng, S = _session()
    try:
        async with S() as s:
            user = await _book(s, "repair4@example.com")
            for p in await list_positions(s, user):
                p.meta = {**(p.meta or {}), "price_as_of": "2026-09-29T20:00:01+00:00"}
            await s.commit()
            p = await lr.held_cash_preview(s, user, card=CARD, legs=lr.parse_legs("BND:270"))
            leg = p["legs"][0]
            assert leg["price"] == pytest.approx(70.23) and leg["price_source"].startswith("position quote")
            assert leg["held_before"] == pytest.approx(13.8723, abs=1e-4)
    finally:
        await eng.dispose()
