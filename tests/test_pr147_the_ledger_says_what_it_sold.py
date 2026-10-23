"""PR 147 -- every trade the ledger records carries its price and its value.

Production, read 2026-10-23: 140 of 440 ledger rows (08-29 .. 10-23) have price
0 and/or value 0 -- every stop and trim (`sell_position`, `trim`: e.g. the 10-23
SOXL stop, 15.78 shares, "₪0"), every funded buy (`buy_funded`: XOM 10-21, so
Holdings shows no "last buy" for XOM), every sleeve-funding sale. The money was
right (each handler reports `cash_added_ils`); the record was not, because the
recorder looks for `price` / `value_ils` and these results did not carry them.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402
from tests.test_a_stop_out_is_not_bought_back import (  # noqa: E402
    SOXL, _accept_the_stop, _book_with_a_fired_stop, _pos)


def _ledger(c):
    return c.get("/api/v1/portfolio/ledger?limit=200").json()["entries"]


def _holes(rows):
    return [r for r in rows if r["side"] in ("Buy", "Sell") and r["quantity"] > 0
            and (r["price"] <= 0 or r["value_ils"] <= 0)]


def test_a_stop_sale_records_its_price_and_value():
    with TestClient(m.app) as c:
        _accept_the_stop(c, _book_with_a_fired_stop(c))
        [row] = [r for r in _ledger(c) if r["ticker"] == "SOXL" and r["side"] == "Sell"]
        assert row["price"] == pytest.approx(100.0) and row["value_ils"] > 0, row
        assert row["quantity"] == pytest.approx(20.0), row


def test_a_cap_trim_records_its_price_and_value():
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("VTI", 10.0), _pos("BND", 10.0, cls="Fixed Income"), _pos("SOXL", 40.0)]})
        c.post("/api/v1/recommendations/restore")
        r = c.post("/api/v1/rules", json={"ticker": "SOXL", "rule_type": "max_weight",
                                          "mode": "pct", "level": 30.0})
        assert r.status_code == 200, r.text
        c.post("/api/v1/rules/evaluate")
        from tests._cards import all_cards
        card = next(x for x in all_cards(c.get("/api/v1/recommendations").json())
                    if (x.get("apply") or {}).get("kind") == "trim"
                    and (x.get("apply") or {}).get("ticker") == "SOXL")
        assert c.post(f"/api/v1/recommendations/{card['id']}/accept").status_code == 200
        rows = [r for r in _ledger(c) if r["ticker"] == "SOXL" and r["side"] == "Sell"]
        assert rows and not _holes(rows), rows


def test_sleeve_funding_records_the_price_of_what_it_sold():
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            _pos("VTI", 60.0), _pos("BND", 60.0, cls="Fixed Income")]})
        assert c.post(f"/api/v1/strategies/{SOXL}/apply?sleeve_pct=20").status_code == 200
        c.post(f"/api/v1/strategies/{SOXL}/load-basket", json={"mode": "fund"})
        rows = [r for r in _ledger(c) if r["source"] == "sleeve_fund"]
        assert rows and not _holes(rows), rows


def test_a_funded_buy_reported_as_one_ticker_records_its_price():
    """`buy_funded` reports `bought` as a ticker string; the recorder took no price."""
    import asyncio

    async def go():
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.config import get_settings
        from app.models.tables import Transaction
        from app.services import recommendations as R
        from app.services.feed_service import ensure_user
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as s:
                u = await ensure_user(s, "pr147@example.com")
                await R._record_apply(s, u, "buy_funded", "rec_x", {
                    "sold": [{"ticker": "BIL", "shares": 2.0, "price": 91.5, "value_ils": 680.0}],
                    "bought": "XOM", "shares": 1.6118, "value_ils": 890.91, "price": 147.2})
                rows = (await s.execute(select(Transaction).where(
                    Transaction.subject == u.email))).scalars().all()
                return {r.ticker: (float(r.price), float(r.value_ils)) for r in rows}
        finally:
            await eng.dispose()
    got = asyncio.run(go())
    assert got["XOM"] == (147.2, 890.91), got
    assert got["BIL"][0] == 91.5, got


def test_a_leg_without_a_price_is_logged_not_silent(caplog):
    import asyncio
    import logging

    async def go():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.config import get_settings
        from app.services import ledger_service as L
        from app.services.feed_service import ensure_user
        eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as s:
                u = await ensure_user(s, "pr147-log@example.com")
                await L.record_leg(s, u, ticker="VTI", txn_type=L.SELL, quantity=1.0, source="test")
        finally:
            await eng.dispose()
    with caplog.at_level(logging.ERROR):
        asyncio.run(go())
    assert any("without a price or value" in r.getMessage() for r in caplog.records)
