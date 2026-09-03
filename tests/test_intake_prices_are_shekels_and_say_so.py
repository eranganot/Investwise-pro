"""Intake wrote a price it could not explain, and one of them was 100x.

Two faults in the same boundary, found together because they are the same
mistake: intake wrote a number without writing what the number meant.

**The 100x.** `to_major_units` says *"Use at every boundary"* in its own
docstring and exactly one boundary used it. Several exchanges quote in a MINOR
unit and say so in the CURRENCY CODE rather than in the price -- Yahoo returns
TEVA.TA as `11030.0` with currency `ILA`, meaning 110.30 shekels.
`add_holding` took `.price` and dropped `.currency`.

What made it dangerous is that it CANCELLED OUT at the moment it was made. A
1,000 ILS purchase booked `1000 / 11030 = 0.0907` shares at 11,030 each, so NAV
read 1,000 and nothing downstream disagreed -- `MARKET_CCY["TASE"]` is "ILS", and
the row carried no currency stamp to contradict. Then the next scheduled price
refresh wrote the correct major-unit price of 110.30 against those 0.0907 shares
and the holding was worth **10 ILS**. An error invisible until a job that did
nothing wrong exposed it.

**The provenance.** `upsert_positions` did `row.meta = meta` -- a wholesale
replace of a dict the price pipeline also writes to. Every re-intake dropped
`price_source`, `price_currency` and `price_as_of`, leaving a price with nothing
saying what currency it was in; and the CSV and broker paths never wrote those
keys at all. A previous fix noticed the replace and rescued exactly one key
(`asset_class_source`), which is how the next one gets lost.

`pricing_service` merges (`{**(p.meta or {}), ...}`). This is the same discipline
on the other writer, plus a deliberate re-stamp: a row whose price came from a
CSV must not still claim it came from Yahoo.

Not financial advice.
"""
import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402

# Yahoo, verified live on 2026-08-29 and recorded in `fx.py`.
TEVA_AGOROT = 11030.0
TEVA_SHEKELS = 110.30
BUDGET_ILS = 1000.0


@pytest.fixture
def a_tel_aviv_quote(monkeypatch):
    """One ticker, quoted the way Tel Aviv is actually quoted."""
    from types import SimpleNamespace as NS

    from app.api.routes import intake as intake_routes

    def _quote(tk):
        if (tk or "").upper() != "TEVA.TA":
            raise LookupError(tk)
        return NS(ticker="TEVA.TA", price=TEVA_AGOROT, currency="ILA", market="TASE")

    monkeypatch.setattr(intake_routes, "guarded_quote", _quote, raising=False)


def _positions(c):
    return {p["ticker"]: p for p in c.get("/api/v1/portfolio").json()["positions"]}


def test_a_tel_aviv_holding_is_booked_in_shekels_not_agorot(a_tel_aviv_quote):
    """The share count is the tell: 9.07 shares, not 0.0907."""
    with TestClient(m.app) as c:
        r = c.post("/api/v1/portfolio/add",
                   json={"ticker": "TEVA.TA", "amount": BUDGET_ILS, "market": "TASE"}).json()
        assert r.get("ok") is True, r

        assert abs(r["price"] - TEVA_SHEKELS) < 0.5, (
            f"booked at {r['price']} -- that is agorot, not shekels")
        expected_qty = BUDGET_ILS / TEVA_SHEKELS
        assert abs(r["quantity"] - expected_qty) < 0.1, (
            f"bought {r['quantity']} shares for {BUDGET_ILS} ILS; at the real price "
            f"that budget buys {expected_qty:.2f}. A share count 100x too small is "
            f"what makes the next price refresh destroy the position")

        held = _positions(c)["TEVA.TA"]
        assert abs(float(held["current_price"]) - TEVA_SHEKELS) < 0.5, (
            "the stored price is still in agorot, so the next refresh will "
            "overwrite it with the major-unit price and the value will collapse")


async def _meta_of(own_session, ticker):
    from sqlalchemy import select

    from app.models.tables import Position
    async with own_session() as s:
        row = (await s.execute(
            select(Position).where(Position.ticker == ticker))).scalars().first()
        return dict(row.meta or {}) if row is not None else None


@pytest.mark.asyncio
async def test_an_intake_price_says_where_it_came_from(a_tel_aviv_quote, own_session):
    """A stored price with no currency stamp is a number nothing can explain."""
    with TestClient(m.app) as c:
        assert c.post("/api/v1/portfolio/add",
                      json={"ticker": "TEVA.TA", "amount": BUDGET_ILS,
                            "market": "TASE"}).json().get("ok") is True

    meta = await _meta_of(own_session, "TEVA.TA")
    assert meta is not None, "the holding was not written at all"
    assert meta.get("price_source") == "intake", (
        f"the row does not say where its price came from: {meta}")
    assert meta.get("price_currency") == "ILS", (
        f"the row does not say what currency its price is in: {meta}")
    assert meta.get("price_as_of"), "the row does not say when it was priced"


@pytest.mark.asyncio
async def test_a_re_intake_does_not_wipe_what_the_price_pipeline_wrote(own_session):
    """`row.meta = meta` replaced the dict. Merge, then re-stamp the price keys.

    The marker is written straight onto the row, which is what
    `pricing_service.refresh_all_positions` does -- it merges its findings into
    `meta` on every run. A re-intake must not be able to erase them.
    """
    from sqlalchemy import select

    from app.models.tables import Position

    base = {"ticker": "SCHD", "market": "NYSE", "asset_class": "Equities",
            "depth": 1, "spot_price": 35.11, "listing_price": 35.11,
            "quantity": 10.0, "cost_basis": 33.36,
            "expected_return_pct": 5, "volatility_pct": 12}

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            c.post("/api/v1/intake/portfolio",
                   json={"entity_name": "Personal", "positions": [base]})

        # What the price pipeline knows and the intake payload does not.
        async with own_session() as s:
            row = (await s.execute(
                select(Position).where(Position.ticker == "SCHD"))).scalars().first()
            row.meta = {**(row.meta or {}), "liquidity_score": 91,
                        "price_source": "yahoo"}
            await s.commit()

        with TestClient(m.app) as c:
            c.post("/api/v1/intake/portfolio",
                   json={"entity_name": "Personal", "positions": [base]})

        meta = await _meta_of(own_session, "SCHD")
        assert meta.get("liquidity_score") == 91, (
            f"a second intake dropped a key the payload said nothing about, so the "
            f"meta dict is being replaced rather than merged: {meta}")
        # And the price keys ARE re-stamped, because intake wrote a new price.
        assert meta.get("price_source") == "intake", (
            f"the row still claims a price source it no longer has: {meta}")
    finally:
        logging.disable(logging.NOTSET)
