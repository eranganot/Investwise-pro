"""Accept must not create or destroy money.

The counterpart to ``test_accept_honesty``, which proves an ADVISORY accept
changes nothing. Nothing proved the same about the accepts that DO change the
book, and three separate bugs lived in that gap for as long as the feature has
existed:

* ``_rebalance_to`` summed ``quantity * current_price`` with no FX, mixing USD
  holdings with the ILS cash row, and wrote the resulting factors back into share
  counts. Measured on a production-shaped book: **+4.8% of NAV per tap**, no
  trade, no cash movement. Two taps ten seconds apart on 2026-08-21 are most of
  an unexplained +7,328 ILS.
* ``_apply_buy_funded`` routed its buy through ``upsert_positions``, which SETS
  ``row.quantity``. Topping up a ticker you already hold replaced the whole
  position with just the shares bought: **-4,388 ILS** in one accept.
* ``_apply_redeploy_cash`` called ``upsert_positions`` with six arguments against
  its three-argument signature -- a TypeError waiting for a leg in a ticker you
  did not already own.

The invariant, stated once: after an accept, NAV equals NAV before minus the tax
the card itself reported. Everything else is a swap. A card that cannot honour
that must abstain, not round.
"""
import logging
import os
from contextlib import asynccontextmanager

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402

TOLERANCE_ILS = 1.0


@asynccontextmanager
async def _own_session():
    """A session on a throwaway NullPool engine created in THIS event loop.

    Reaching for ``AsyncSessionLocal`` here passes on SQLite and fails on
    Postgres: ``TestClient`` has already driven the app's shared pooled engine
    in its own loop, so asyncpg rejects the connection with "attached to a
    different loop" while SQLite shrugs. That is the exact trap CLAUDE.md lists
    under Known sharp edges, and conftest's ``_isolate_db`` fixture already
    solves it the same way. It cost this file a red `test-postgres` on 5a6dfef
    while `test` and `lint` were both green -- which is precisely what that job
    exists to catch.
    """
    from sqlalchemy.ext.asyncio import (
        AsyncSession, async_sessionmaker, create_async_engine,
    )
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            yield session
    finally:
        await eng.dispose()


PRICES = {"V": 371.04, "MSFT": 483.24, "SCHD": 35.11, "TQQQ": 71.17}


@pytest.fixture
def pinned_prices(monkeypatch):
    """Quote the seeded prices back.

    A buy legitimately refreshes ``current_price`` on the position it lands in,
    so a stub provider that disagrees with the seeded price moves NAV for a
    reason that has nothing to do with conservation. Holding price constant
    leaves the trade as the only thing that can move the book -- which is the
    only thing under test here.
    """
    from types import SimpleNamespace as NS

    from app.providers import registry

    def _quote(tk):
        tk = (tk or "").upper()
        if tk not in PRICES:
            raise LookupError(tk)
        return NS(ticker=tk, price=PRICES[tk], currency="USD",
                  market="NYSE" if tk == "SCHD" else "NASDAQ")

    monkeypatch.setattr(registry, "guarded_quote", _quote)
    return PRICES


def _seed_usd(c):
    """A USD-priced book with an ILS cash row -- the shape that exposed the FX bug.

    All four names are NASDAQ/NYSE, so every value needs an FX hop, while CASH is
    ILS-native at 1.0. Any code that adds the two without normalising shows up
    here and nowhere else.
    """
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
        {"ticker": "TQQQ", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
         "spot_price": 71.17, "listing_price": 71.17, "quantity": 20.1041,
         "cost_basis": 77.35, "expected_return_pct": 12, "volatility_pct": 60},
    ]})
    c.post("/api/v1/portfolio/cash", json={"amount_ils": 621.79})
    c.put("/api/v1/plan", json={"objective": "Grow", "risk_tolerance": "High",
                                "horizon_years": 6, "target_amount": 137547})


def _nav(c):
    return c.get("/api/v1/portfolio").json()["nav_ils"]


# --------------------------------------------------------------------------- #
# the named regressions, each pinned on its own so a failure names its cause
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_rebalance_to_objective_does_not_mint_shares():
    """`_rebalance_to` reshuffles weights. A reshuffle has no NAV of its own."""
    from sqlalchemy import select

    from app.models.tables import User
    from app.services.intake_service import list_positions
    from app.services.recommendations import _rebalance_to

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed_usd(c)
            before = _nav(c)
            async with _own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                await _rebalance_to(s, user, await list_positions(s, user), "Grow")
                await s.commit()
            after = _nav(c)
            assert abs(after - before) < TOLERANCE_ILS, (
                f"rebalance moved NAV by {after - before:+.2f} ILS with no trade "
                f"({before:.2f} -> {after:.2f})")
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.asyncio
async def test_buying_more_of_a_held_ticker_adds_to_it(pinned_prices):
    """A funded buy into a position you already hold must BLEND, never replace."""
    from sqlalchemy import select

    from app.models.tables import User
    from app.services.recommendations import _apply_buy_funded

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed_usd(c)
            before = _nav(c)
            held_before = next(p for p in c.get("/api/v1/portfolio").json()["positions"]
                               if p["ticker"] == "SCHD")
            async with _own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                detail = await _apply_buy_funded(s, user, {
                    "kind": "buy_funded", "ticker": "SCHD", "market": "NYSE",
                    "asset_class": "Equities", "amount_ils": 2000.0,
                    "from_cash_ils": 0.0,
                    "sells": [{"ticker": "V", "market": "NASDAQ", "shares": 2,
                               "value_ils": 2214.81, "asset_class": "Equities"}]})
                await s.commit()
            after_rows = c.get("/api/v1/portfolio").json()["positions"]
            held_after = next(p for p in after_rows if p["ticker"] == "SCHD")

            assert float(held_after["quantity"]) > float(held_before["quantity"]), (
                "SCHD shrank when we bought MORE of it -- upsert_positions replaced "
                "the position instead of adding to it")
            leak = _nav(c) - before + float(detail.get("tax_ils") or 0.0)
            assert abs(leak) < TOLERANCE_ILS, f"buy_funded leaked {leak:+.2f} ILS"
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.asyncio
async def test_a_buy_never_spends_cash_that_is_not_there(pinned_prices):
    """`from_cash_ils` is planned against an older book. It may not overdraw.

    The debit used to be clamped at zero AFTER the full amount had been bought,
    so a stale card funded its purchase out of arithmetic.
    """
    from sqlalchemy import select

    from app.models.tables import User
    from app.services.intake_service import get_cash
    from app.services.recommendations import _apply_buy_funded

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed_usd(c)
            before = _nav(c)
            async with _own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                # Claim ten times the cash the book actually holds.
                detail = await _apply_buy_funded(s, user, {
                    "kind": "buy_funded", "ticker": "SCHD", "market": "NYSE",
                    "asset_class": "Equities", "amount_ils": 6000.0,
                    "from_cash_ils": 6000.0, "sells": []})
                await s.commit()
                cash_after = await get_cash(s, user)

            assert cash_after >= -0.01, "cash went negative"
            assert float(detail.get("from_cash_ils") or 0.0) <= 621.79 + 0.01, (
                "spent more cash than the book held")
            leak = _nav(c) - before + float(detail.get("tax_ils") or 0.0)
            assert abs(leak) < TOLERANCE_ILS, (
                f"an overdrawn buy created {leak:+.2f} ILS out of the clamp")
    finally:
        logging.disable(logging.NOTSET)


# --------------------------------------------------------------------------- #
# the general net: whatever the engine offers today, accepting it must balance
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_every_actionable_card_on_a_live_book_conserves_nav(pinned_prices):
    """Sweep whatever `build_recommendations` actually produces.

    Deliberately not parametrized over `_ACTIONABLE_KINDS`: a hand-built spec
    tests the handler, this tests the handler *on the card the app really
    generated*, which is where the mismatch between the two lived.
    """
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed_usd(c)
            recs = c.get("/api/v1/recommendations").json()["recommendations"]
            checked = 0
            for r in recs:
                if not r.get("actionable"):
                    continue
                before = _nav(c)
                res = c.post(f"/api/v1/recommendations/{r['id']}/accept")
                if res.status_code != 200:
                    continue
                body = res.json()
                leak = _nav(c) - before + float(body.get("tax_ils") or 0.0)
                assert abs(leak) < TOLERANCE_ILS, (
                    f"{(r.get('apply') or {}).get('kind')} card {r['title']!r} "
                    f"leaked {leak:+.2f} ILS")
                checked += 1
            assert checked, "no actionable card was produced -- this test proved nothing"
    finally:
        logging.disable(logging.NOTSET)
