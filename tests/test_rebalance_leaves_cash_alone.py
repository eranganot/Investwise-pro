"""A rebalance may not spend your cash, and an accept must say what it did.

Three defects, all found by instrumenting an accept rather than by reading it,
and all invisible to every test that existed:

* `_rebalance_to` classified the CASH row as the asset class "Cash", found no
  `Cash` key in the book target (there is none on any objective -- Grow is
  80/10/10 Equities/Fixed Income/Commodities), computed a desired weight of
  zero, and set the balance to zero. The money went into the equity legs. NAV
  was conserved, so the function's own guard passed. What it actually did was
  buy securities with the whole cash balance: no trade legs, no ledger entry,
  no tax, and a card whose text says only "Sell about X of Equities to move
  toward 80%". It also drove the balance through the app's own cash floor
  (`funding_service.cash_floor_ils`), which this path never consulted.

* Every MONEY column is `Numeric(18, 4)`, and the writers handed it six decimal
  places. Postgres rounds on write; the identity-mapped object keeps all six
  until it is expired. So a conservation check that reads the ORM object
  measures a book the database never had -- and the divergence is invisible on
  SQLite, which keeps whatever it is given. Only `test-postgres` could see it.

* The conservation invariant was enforced inside ONE of the twelve actionable
  handlers. The other eleven changed money with nothing checking them; the only
  thing standing behind them was a sweep over whatever cards the engine happened
  to emit that run.
"""
import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402

CASH_ILS = 621.79


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "V", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
         "spot_price": 371.04, "listing_price": 371.04, "quantity": 9.711,
         "cost_basis": 340.51, "expected_return_pct": 7, "volatility_pct": 20},
        {"ticker": "MSFT", "market": "NASDAQ", "asset_class": "Equities", "depth": 1,
         "spot_price": 483.24, "listing_price": 483.24, "quantity": 4.7122,
         "cost_basis": 462.40, "expected_return_pct": 7, "volatility_pct": 20},
        {"ticker": "SCHD", "market": "NYSE", "asset_class": "Equities", "depth": 1,
         "spot_price": 35.11, "listing_price": 35.11, "quantity": 57.7734,
         "cost_basis": 33.36, "expected_return_pct": 5, "volatility_pct": 12},
    ]})
    c.post("/api/v1/portfolio/cash", json={"amount_ils": CASH_ILS})
    c.put("/api/v1/plan", json={"objective": "Grow", "risk_tolerance": "High",
                                "horizon_years": 6, "target_amount": 137547})


def _cash(c):
    rows = c.get("/api/v1/portfolio").json()["positions"]
    return next((float(p["quantity"]) for p in rows if p["ticker"] == "CASH"), 0.0)


@pytest.mark.asyncio
async def test_rebalance_does_not_spend_the_cash_balance(own_session):
    """Accepting a rebalance is a reshuffle. It is not permission to buy."""
    from sqlalchemy import select

    from app.models.tables import User
    from app.services.intake_service import list_positions
    from app.services.recommendations import _rebalance_to

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            assert _cash(c) == pytest.approx(CASH_ILS, abs=0.01), "seed did not take"
            async with own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                await _rebalance_to(s, user, await list_positions(s, user), "Grow")
                await s.commit()
            assert _cash(c) == pytest.approx(CASH_ILS, abs=0.01), (
                "the rebalance spent the cash balance -- 'Cash' is not in any book "
                "target, so a desired weight of 0.0 zeroed the row and the money "
                "went into the equity legs as an unrecorded, untaxed purchase")
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.asyncio
async def test_rebalance_still_reaches_the_target_over_investable_value(own_session):
    """Excluding cash must not stop the rebalance doing its actual job."""
    from sqlalchemy import select

    from app.models.tables import User
    from app.services.allocation_mix import classify
    from app.services.fx import book_nav_ils, position_value_ils
    from app.services.intake_service import is_cash_position, list_positions
    from app.services.recommendations import _rebalance_to

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            async with own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                await _rebalance_to(s, user, await list_positions(s, user), "Grow")
                await s.commit()
                rows = await list_positions(s, user)
            invested = [p for p in rows if not is_cash_position(
                p.ticker, p.meta if isinstance(p.meta, dict) else None)]
            nav = book_nav_ils(invested)
            eq = sum(position_value_ils(p) for p in invested
                     if classify(p.ticker, p.market, (p.meta or {}).get("asset_class")) == "Equities")
            # Every investable holding on this book is Equities and the target has
            # no other class present, so the whole invested value stays in it.
            assert eq == pytest.approx(nav, rel=1e-6)
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.asyncio
async def test_every_accept_reports_what_it_did_to_nav():
    """The measurement is the deliverable: an accept that moves the book says so.

    A bare "NAV moved by +739.21" with nothing attached is what the previous
    sighting left behind, and it cost a session. Every accept now carries
    before/after/drift, so the next one arrives with its own evidence.
    """
    from app.services.recommendations import _ACTIONABLE_KINDS

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            recs = c.get("/api/v1/recommendations").json()["recommendations"]
            checked = 0
            for r in recs:
                if not r.get("actionable"):
                    continue
                res = c.post(f"/api/v1/recommendations/{r['id']}/accept")
                if res.status_code != 200:
                    continue
                body = res.json()
                if body.get("applied") not in _ACTIONABLE_KINDS:
                    continue
                for field in ("nav_before_ils", "nav_after_ils", "nav_drift_ils"):
                    assert field in body, (
                        f"{body.get('applied')} accepted without reporting {field}; "
                        "an accept that changes money must say what it changed")
                assert abs(float(body["nav_drift_ils"])) < 1.0, (
                    f"{body['applied']} card {r['title']!r} moved NAV by "
                    f"{body['nav_drift_ils']:+.2f} ILS beyond the tax it reported")
                checked += 1
            assert checked, "no actionable card was produced -- this test proved nothing"
    finally:
        logging.disable(logging.NOTSET)


def test_money_writes_are_quantised_to_the_stored_scale():
    """What is computed is what is stored, on both databases."""
    from decimal import Decimal

    from app.models.tables import MONEY, money

    assert MONEY.scale == 4, "the quantiser reads the column; update it together"
    # The exact value _rebalance_to used to write, and what Postgres kept of it.
    assert money(4.7122764) == Decimal("4.7123")
    assert money(20.1041) == Decimal("20.1041")
    # No exponent notation, no silent widening: the result is always the scale
    # the column declares, so the ORM object and the row agree digit for digit.
    assert str(money(1)) == "1.0000"
    assert money(Decimal("0.00004")) == Decimal("0.0000")
