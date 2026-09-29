"""Realigning the core must pay for its buys out of cash that exists.

``_apply_core_to_basket`` topped its buy budget up from cash with
``credit_cash(session, user, -cash_top)``. ``credit_cash`` returns 0 and does
nothing for any amount <= 0, and the handler only checked ``is not None`` -- so
the budget grew by ``cash_top`` while the balance never moved. Every realign
that needed cash bought its ETFs with money the book did not have, and any
unspent remainder was then credited to cash a second time.

Seen on production 2026-09-29: one accept (rec_2d1727, 07:42 UTC) bought
13.8723 BND + 0.601 VTI for ~3,678 ILS; cash went UP overall, and NAV rose
3,587 ILS overnight while no market was open. Live since PR 39 (2026-09-02).

These tests apply the action to a real book and re-measure NAV and cash, rather
than inspecting the handler's source -- every earlier test of this path read
its text, which is why none of them could see it.
"""
import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402

TOLERANCE_ILS = 1.0
PRICES = {"V": 371.04, "SCHD": 35.11, "BND": 70.28}
CASH = 621.79


@pytest.fixture
def quotes(monkeypatch):
    from types import SimpleNamespace as NS

    from app.providers import registry
    from app.services import pricing_service, strategy_service

    def _quote(tk):
        tk = (tk or "").upper()
        if tk not in PRICES:
            raise LookupError(tk)
        return NS(ticker=tk, price=PRICES[tk], currency="USD", market="NYSE")

    for mod in (registry, pricing_service, strategy_service):
        monkeypatch.setattr(mod, "guarded_quote", _quote, raising=False)
    return _quote


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "V", "market": "NASDAQ", "asset_class": "Equities", "depth": 1,
         "spot_price": 371.04, "listing_price": 371.04, "quantity": 9.711,
         "cost_basis": 340.51, "expected_return_pct": 7, "volatility_pct": 20},
        {"ticker": "SCHD", "market": "NYSE", "asset_class": "Equities", "depth": 1,
         "spot_price": 35.11, "listing_price": 35.11, "quantity": 57.7734,
         "cost_basis": 33.36, "expected_return_pct": 5, "volatility_pct": 12},
    ]})
    c.post("/api/v1/portfolio/cash", json={"amount_ils": CASH})
    c.put("/api/v1/plan", json={"objective": "Grow", "risk_tolerance": "High",
                                "horizon_years": 6, "target_amount": 137547})


def _portfolio(c):
    return c.get("/api/v1/portfolio").json()


def _plan(legs, cash_used):
    return {"ok": True, "legs": legs, "cash_used_ils": cash_used,
            "shortfall_ils": 0.0, "sleeve_values_ils": {}}


async def _apply(own_session, monkeypatch, plan):
    from sqlalchemy import select

    from app.models.tables import User
    from app.services import recommendations as R

    async def _fixed_plan(*_a, **_k):
        return plan

    async def _no_target(*_a, **_k):
        return {}

    monkeypatch.setattr(R, "_core_plan", _fixed_plan)
    monkeypatch.setattr(R, "_book_target", _no_target)
    async with own_session() as s:
        user = (await s.execute(select(User))).scalars().first()
        detail = await R._apply_core_to_basket(s, user, {"objective": "Grow"})
        await s.commit()
    return detail


@pytest.mark.asyncio
async def test_a_cash_funded_realign_debits_the_cash_it_spends(
        quotes, own_session, monkeypatch):
    """Buy 400 ILS of BND from cash only: cash falls by 400, NAV does not move."""
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            before = _portfolio(c)
            detail = await _apply(own_session, monkeypatch, _plan(
                [{"action": "buy", "ticker": "BND", "ils": 400.0,
                  "market": "NYSE", "asset_class": "Fixed Income"}], 400.0))
            after = _portfolio(c)

            assert detail.get("bought"), f"nothing was bought: {detail}"
            assert abs(after["cash_ils"] - (before["cash_ils"] - 400.0)) < TOLERANCE_ILS, (
                f"cash went {before['cash_ils']} -> {after['cash_ils']}; a 400 ILS "
                f"purchase paid from cash must take 400 ILS out of it")
            assert abs(after["nav_ils"] - before["nav_ils"]) < TOLERANCE_ILS, (
                f"NAV moved {after['nav_ils'] - before['nav_ils']:+.2f} ILS with no "
                f"price change -- the realign created or destroyed money")
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.asyncio
async def test_a_realign_never_spends_more_cash_than_the_book_holds(
        quotes, own_session, monkeypatch):
    """A plan built against an earlier, richer book is clamped to today's balance."""
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            before = _portfolio(c)
            await _apply(own_session, monkeypatch, _plan(
                [{"action": "buy", "ticker": "BND", "ils": 5000.0,
                  "market": "NYSE", "asset_class": "Fixed Income"}], 5000.0))
            after = _portfolio(c)

            assert after["cash_ils"] >= -0.01, f"cash went negative: {after['cash_ils']}"
            assert abs(after["nav_ils"] - before["nav_ils"]) < TOLERANCE_ILS, (
                f"NAV moved {after['nav_ils'] - before['nav_ils']:+.2f} ILS: a buy "
                f"larger than the cash balance was funded by arithmetic")
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.asyncio
async def test_sell_funded_realign_with_cash_top_up_conserves_nav(
        quotes, own_session, monkeypatch):
    """Sell part of V, top up from cash, buy BND: only tax may leave the book."""
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            before = _portfolio(c)
            detail = await _apply(own_session, monkeypatch, _plan(
                [{"action": "sell", "ticker": "V", "ils": 1500.0},
                 {"action": "buy", "ticker": "BND", "ils": 1800.0,
                  "market": "NYSE", "asset_class": "Fixed Income"}], 300.0))
            after = _portfolio(c)

            tax = float(detail.get("tax_ils") or 0.0)
            drift = after["nav_ils"] - before["nav_ils"] + tax
            assert abs(drift) < TOLERANCE_ILS, (
                f"NAV moved {after['nav_ils'] - before['nav_ils']:+.2f} ILS against "
                f"{tax:.2f} ILS of tax: {drift:+.2f} ILS unaccounted for")
            assert after["cash_ils"] < before["cash_ils"], (
                "the cash top-up was spent but the balance did not fall")
    finally:
        logging.disable(logging.NOTSET)


@pytest.mark.asyncio
async def test_credit_cash_refuses_a_negative_amount(own_session):
    """`credit_cash` adds. A negative amount used to be silently ignored, which
    is how a debit turned into a no-op. It must now fail loudly."""
    from sqlalchemy import select

    from app.models.tables import User
    from app.services.intake_service import credit_cash

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            async with own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                with pytest.raises(ValueError):
                    await credit_cash(s, user, -10.0)
                assert await credit_cash(s, user, 0.0) == 0.0
    finally:
        logging.disable(logging.NOTSET)


def test_a_realign_buy_reports_what_it_cost():
    """The ledger records `value_ils`/`amount_ils`/`ils` for a buy. Realign buys
    reported only `ils`, which `record_legs` did not read, so every one went into
    the ledger at 0 ILS and could not be audited."""
    import inspect

    from app.services import ledger_service
    from app.services import recommendations as R

    assert '"value_ils": round(spend, 2)' in inspect.getsource(R._apply_core_to_basket)
    assert 'leg.get("ils")' in inspect.getsource(ledger_service.record_legs)


def test_the_ledger_is_readable():
    with TestClient(m.app) as c:
        _seed(c)
        r = c.get("/api/v1/portfolio/ledger?limit=5")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "entries" in body and body["count"] == len(body["entries"])
        assert isinstance(body["snapshots"], list)
