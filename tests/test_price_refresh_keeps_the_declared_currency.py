"""A price refresh may not redenominate a holding. CI #169 and #178, finally located.

`test_cash.py::test_accepting_a_sell_credits_cash_with_a_sane_cost_basis` failed
twice in CI with the *identical* assertion -- `192360.0 <= 41000.0 + 1.0` -- and
could not be reproduced from the number alone. The row dump added after the
first sighting is what located it:

    BBB qty=10.0  basis=100.0 invested=3710.0
    AAA qty=107.0 basis=100.0 invested=39697.0
    CASH qty=148953.0 basis=1.0 invested=148953.0

Both holdings are seeded on **TASE at 100 ILS**, so BBB's invested should read
1,000. It reads 3,710 -- exactly 1,000 x 3.71, which is
``BuiltinFXProvider._BASE[("USD","ILS")]``: the HARDCODED rate the test
providers use, not a market rate. (Today's USD/ILS is ~2.98, so in production
the multiplier would be ~2.97. Roughly triple either way.) Every row had been
redenominated.

**Root cause.** The provider decides the market from the TICKER, not from the
holding. `BuiltinMarketDataProvider.get_quote` is literally
``market = "TASE" if ticker.upper().startswith("TA") else "NYSE"``
(`builtin.py:37`), and returns that market's currency. `pricing_service` stamped
`price_currency` from the quote, so the provider's guess **overruled the market
the user declared in the holdings form**.

Measured on the exact book below: one refresh takes NAV from 41,000 to 235,687
and invested from 41,000 to 152,110, both exactly x3.71. The CI dump's cash
figure of 148,953 reconstructs to the shekel: 293 shares at the builtin's
synthetic 149.37, times 3.71, less 25% CGT on the gain.

It was intermittent only because it needs a refresh to land inside the test --
the scheduler primes one on startup, and Postgres runs are slow enough for the
timing to differ. The arithmetic was always deterministic, and it is reachable in
production by anyone holding a Tel Aviv or London name under a ticker the
provider reads as American.
"""
import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402

# The book from test_cash.py, unchanged. Two Tel Aviv holdings at 100 ILS.
TASE_BOOK = {"entity_name": "Personal", "positions": [
    {"ticker": "AAA", "market": "TASE", "asset_class": "Equities", "depth": 3,
     "spot_price": 100, "listing_price": 100, "quantity": 400, "cost_basis": 100,
     "expected_return_pct": 7, "volatility_pct": 14},
    {"ticker": "BBB", "market": "TASE", "asset_class": "Equities", "depth": 2,
     "spot_price": 100, "listing_price": 100, "quantity": 10, "cost_basis": 100,
     "expected_return_pct": 5, "volatility_pct": 10},
]}


async def _refresh(own_session):
    from sqlalchemy import select

    from app.models.tables import User
    from app.services import pricing_service
    from app.services.intake_service import list_positions

    async with own_session() as s:
        user = (await s.execute(select(User))).scalars().first()
        rows = await list_positions(s, user)
        res = await pricing_service.refresh_all_positions(s, rows)
        await s.commit()
    return res


@pytest.mark.asyncio
async def test_a_refresh_does_not_redenominate_a_tel_aviv_holding(own_session):
    """The one that was live. NAV must not move by the FX rate on a reprice."""
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            c.post("/api/v1/intake/portfolio", json=TASE_BOOK)
            before = c.get("/api/v1/portfolio").json()
            assert before["nav_ils"] == pytest.approx(41000.0, abs=1.0)
            assert before["invested_ils"] == pytest.approx(41000.0, abs=1.0)

            await _refresh(own_session)

            after = c.get("/api/v1/portfolio").json()
            for row in after["positions"]:
                assert row["currency"] == "ILS", (
                    f"{row['ticker']} is held on {row['market']} and came back "
                    f"stamped {row['currency']} -- the provider's guess about the "
                    "ticker overruled the market the holding is declared on")
            assert after["invested_ils"] == pytest.approx(41000.0, abs=1.0), (
                f"invested went {before['invested_ils']} -> {after['invested_ils']} "
                f"on a REPRICE (x{after['invested_ils'] / before['invested_ils']:.2f}). "
                "Selling nothing and buying nothing cannot change what you put in.")
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.asyncio
async def test_the_conflicting_quote_is_refused_and_reported(own_session):
    """Refused, and SAID. A quote silently dropped is the other failure mode."""
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            c.post("/api/v1/intake/portfolio", json=TASE_BOOK)
            res = await _refresh(own_session)

            assert res.get("currency_conflicts") == 2, (
                f"expected both TASE rows to be refused, got {res.get('currency_conflicts')}")
            assert res.get("updated") == 0, (
                "a quote in the wrong currency is not a quote about this holding "
                "and its price must not be written either")
            row = c.get("/api/v1/portfolio").json()["positions"][0]
            conflict = row.get("price_currency_conflict")
            assert conflict, "the row does not say why its price stopped updating"
            assert conflict["declared"] == "ILS"
            assert conflict["quoted"] == "USD"
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.asyncio
async def test_a_row_already_redenominated_is_restored(own_session):
    """Refusing new quotes does nothing for the rows already carrying the damage."""
    from sqlalchemy import select

    from app.models.tables import User
    from app.services.intake_service import list_positions

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            c.post("/api/v1/intake/portfolio", json=TASE_BOOK)
            # Forge exactly what an earlier refresh did: TASE row stamped USD.
            async with own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                for p in await list_positions(s, user):
                    p.meta = {**(p.meta or {}), "price_currency": "USD"}
                await s.commit()
            damaged = c.get("/api/v1/portfolio").json()
            assert damaged["invested_ils"] == pytest.approx(152110.0, abs=1.0), (
                "the forged rows should read x3.71 -- if not, this proves nothing")

            res = await _refresh(own_session)

            assert res.get("repaired_currency_rows") == 2
            healed = c.get("/api/v1/portfolio").json()
            assert healed["invested_ils"] == pytest.approx(41000.0, abs=1.0)
            for row in healed["positions"]:
                assert row["currency"] == "ILS"
                # The price was written by a provider answering about something
                # else. There is no arithmetic that recovers the right number
                # from it, so the row says so rather than being quietly trusted.
                assert row["price_unverified"] is True
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.asyncio
async def test_a_us_holding_still_reprices_normally(own_session):
    """The guard must not stop the case it does not apply to."""
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            c.post("/api/v1/intake/portfolio", json={
                "entity_name": "Personal", "positions": [
                    {"ticker": "AAA", "market": "NYSE", "asset_class": "Equities",
                     "depth": 3, "spot_price": 100, "listing_price": 100,
                     "quantity": 400, "cost_basis": 100,
                     "expected_return_pct": 7, "volatility_pct": 14}]})
            res = await _refresh(own_session)
            assert res.get("currency_conflicts") == 0
            assert res.get("updated") == 1, "a NYSE holding quoted in USD must reprice"
            row = c.get("/api/v1/portfolio").json()["positions"][0]
            assert row["currency"] == "USD"
            assert float(row["current_price"]) != 100.0, "the price did not update"
    finally:
        logging.disable(logging.NOTSET)
