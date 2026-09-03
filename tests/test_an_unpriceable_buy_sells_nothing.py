"""A buy that cannot be priced must not sell anything.

``_apply_buy_funded`` executed every sell leg FIRST and only then asked for a
price on the thing it was buying. When that quote failed it credited the
proceeds to cash and said so -- but the positions were already gone, the capital
gains tax was already realized, and the card had claimed a purchase it never
made. The book the user got back was not the book either the card or the
executor described.

``_apply_core_to_basket`` was written with the opposite order for exactly this
reason, and says so in its own docstring: *"PRICES BEFORE IT SELLS.
`_apply_buy_funded` sells first and, if the buy cannot be priced afterwards,
leaves the proceeds sitting in cash... That is survivable for a single swap;
here it would mean selling three positions and leaving the whole core in cash
because one quote timed out."*

Survivable is not the same as correct. Today carries two ``buy_funded`` cards at
any time, and the rule -- a card may not claim a portfolio change it does not
produce -- does not have a size threshold. This pins the same ordering on the
older path: price every leg first, refuse before anything is sold.
"""
import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402

TOLERANCE_ILS = 1.0

# The book's own names, quoted back at the price they were seeded with, so the
# only thing that can move the book is a trade.
PRICES = {"V": 371.04, "SCHD": 35.11}

# The instrument the card wants to buy. No provider answers for it -- the
# commodity tickers the screener proposes are exactly the ones most likely to
# be missing from a given provider, which is how this path gets reached.
UNPRICEABLE = "USO"


@pytest.fixture
def quotes_that_fail_for_the_buy(monkeypatch):
    """Every held name prices; the buy ticker raises.

    Patched in every module that holds its own reference. `pricing_service` and
    `strategy_service` both bind `guarded_quote` at import time, so a patch on
    the registry alone never reaches them -- and both of them write prices.
    """
    from types import SimpleNamespace as NS

    from app.providers import registry
    from app.services import pricing_service, strategy_service

    def _quote(tk):
        tk = (tk or "").upper()
        if tk not in PRICES:
            raise LookupError(tk)
        return NS(ticker=tk, price=PRICES[tk], currency="USD",
                  market="NYSE" if tk == "SCHD" else "NASDAQ")

    for mod in (registry, pricing_service, strategy_service):
        monkeypatch.setattr(mod, "guarded_quote", _quote, raising=False)
    return _quote


def _seed(c):
    """Two USD holdings and an ILS cash row -- the shape a funded buy trades on."""
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "V", "market": "NASDAQ", "asset_class": "Equities", "depth": 1,
         "spot_price": 371.04, "listing_price": 371.04, "quantity": 9.711,
         "cost_basis": 340.51, "expected_return_pct": 7, "volatility_pct": 20},
        {"ticker": "SCHD", "market": "NYSE", "asset_class": "Equities", "depth": 1,
         "spot_price": 35.11, "listing_price": 35.11, "quantity": 57.7734,
         "cost_basis": 33.36, "expected_return_pct": 5, "volatility_pct": 12},
    ]})
    c.post("/api/v1/portfolio/cash", json={"amount_ils": 621.79})
    c.put("/api/v1/plan", json={"objective": "Grow", "risk_tolerance": "High",
                                "horizon_years": 6, "target_amount": 137547})


def _book(c):
    """ticker -> (quantity, current_price). Enough to prove nothing moved."""
    return {p["ticker"]: (p.get("quantity"), p.get("current_price"))
            for p in c.get("/api/v1/portfolio").json()["positions"]}


def _nav(c):
    return c.get("/api/v1/portfolio").json()["nav_ils"]


@pytest.mark.asyncio
async def test_a_buy_that_cannot_be_priced_sells_nothing(
        quotes_that_fail_for_the_buy, own_session):
    """The whole action refuses. No sell, no tax, no cash movement, no position."""
    from sqlalchemy import select

    from app.models.tables import User
    from app.services.recommendations import _apply_buy_funded

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            before_book, before_nav = _book(c), _nav(c)

            async with own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                detail = await _apply_buy_funded(s, user, {
                    "kind": "buy_funded", "ticker": UNPRICEABLE, "market": "NYSE",
                    "asset_class": "Commodities", "amount_ils": 2000.0,
                    "from_cash_ils": 0.0,
                    "sells": [{"ticker": "V", "market": "NASDAQ", "shares": 2,
                               "value_ils": 2214.81, "asset_class": "Equities"}]})
                await s.commit()

            after_book, after_nav = _book(c), _nav(c)

            assert not detail.get("bought"), (
                f"claimed to buy {detail.get('bought')} without a price for it")
            assert not detail.get("sold"), (
                f"sold {detail.get('sold')} to fund a buy that never happened -- "
                f"the proceeds are sitting in cash and the card said otherwise")
            assert not float(detail.get("tax_ils") or 0.0), (
                f"realized {detail.get('tax_ils')} ILS of capital gains tax for a "
                f"trade that did not happen")
            assert after_book == before_book, (
                f"the book moved while nothing was bought:\n"
                f"  before {before_book}\n  after  {after_book}")
            assert abs(after_nav - before_nav) < TOLERANCE_ILS, (
                f"NAV moved {after_nav - before_nav:+.2f} ILS on a refused action")
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.asyncio
async def test_a_fee_swap_that_cannot_price_its_replacement_sells_nothing(
        quotes_that_fail_for_the_buy, own_session):
    """The same defect, with the whole position at stake.

    `_apply_fee_swap` sells the ENTIRE holding, then priced the replacement. On
    failure it credited the proceeds to cash -- so "swap into a cheaper fund"
    became a full exit the card never proposed, with the tax realized.
    """
    from sqlalchemy import select

    from app.models.tables import User
    from app.services.intake_service import list_positions
    from app.services.recommendations import _apply_fee_swap

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            before_book, before_nav = _book(c), _nav(c)

            async with own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                rows = await list_positions(s, user)
                by_ticker = {(p.ticker or "").upper(): p for p in rows}
                detail = await _apply_fee_swap(s, user, by_ticker, {
                    "kind": "fee_swap", "sell": "V", "buy": UNPRICEABLE,
                    "asset_class": "Equities"})
                await s.commit()

            assert not detail.get("swapped"), "claimed a swap it could not price"
            assert not detail.get("sold"), (
                f"sold {detail.get('sold')} outright for a swap that never happened")
            assert _book(c) == before_book, (
                f"the book moved on a refused swap:\n"
                f"  before {before_book}\n  after  {_book(c)}")
            assert abs(_nav(c) - before_nav) < TOLERANCE_ILS, (
                f"NAV moved {_nav(c) - before_nav:+.2f} ILS on a refused swap")
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.asyncio
async def test_a_priceable_buy_still_executes(quotes_that_fail_for_the_buy, own_session):
    """The guard above must refuse the unpriceable case and only that case.

    Pricing first is a reordering, not a new veto: a buy whose ticker quotes must
    still sell its funding legs and land the shares.
    """
    from sqlalchemy import select

    from app.models.tables import User
    from app.services.recommendations import _apply_buy_funded

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            held_before = _book(c)["SCHD"][0]

            async with own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                detail = await _apply_buy_funded(s, user, {
                    "kind": "buy_funded", "ticker": "SCHD", "market": "NYSE",
                    "asset_class": "Equities", "amount_ils": 2000.0,
                    "from_cash_ils": 0.0,
                    "sells": [{"ticker": "V", "market": "NASDAQ", "shares": 2,
                               "value_ils": 2214.81, "asset_class": "Equities"}]})
                await s.commit()

            assert detail.get("bought") == "SCHD", f"the buy did not happen: {detail}"
            assert float(_book(c)["SCHD"][0]) > float(held_before), (
                "SCHD did not grow after a funded buy into it")
    finally:
        logging.disable(logging.NOTSET)
