"""The per-holding chart: price, and what YOUR position actually did.

Pinned:
  * the value line uses the shares you held AT EACH MOMENT, reconstructed from
    the trade ledger -- a top-up is a step in the line, and it is booked as a
    trade, not as a gain;
  * the value line starts where the record starts (position opened / ledger
    began) and never before -- no backfill of today's shares through the past;
  * a ledger that cannot reconcile with today's count hides the value line and
    says why, instead of drawing a share count that cannot be right;
  * cash is refused before any market call.

Not financial advice.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import app.providers.registry as registry
from app.main import app
from app.services.feed_service import SUPERADMIN_EMAIL

NOW = datetime.now(timezone.utc)


def _daily(n=30, start=100.0, step=1.0):
    today = date.today()
    return [((today - timedelta(days=n - 1 - i)).isoformat(), start + step * i) for i in range(n)]


@pytest.fixture()
def client(monkeypatch):
    series = _daily()
    monkeypatch.setattr(registry, "guarded_history", lambda tk, days=252: series[-days:])
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "QQQ", "market": "TASE", "asset_class": "Equities", "depth": 2,
             "spot_price": 129.0, "listing_price": 129.0, "quantity": 10, "cost_basis": 100.0,
             "expected_return_pct": 8, "volatility_pct": 18}]})
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 500.0, "mode": "set"})
        yield c, series


def _db(fn):
    """A throwaway NullPool engine in THIS loop (CLAUDE.md, cross-loop sessions):
    the shared engine's connections belong to TestClient's loop, and asyncpg
    refuses them here even though SQLite does not."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings

    async def _run():
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                await fn(s)
                await s.commit()
        finally:
            await engine.dispose()
    asyncio.run(_run())


def _backdate_position(days):
    from sqlalchemy import update

    from app.models.tables import Position

    async def f(s):
        await s.execute(update(Position).where(Position.ticker == "QQQ")
                        .values(created_at=NOW - timedelta(days=days)))
    _db(f)


def _ledger_began(days_ago):
    """The ledger's first row, as reconcile's baseline writes it. Production's
    ledger has rows from 2026-08; without one, the record starts at the first
    leg a test adds and the line (correctly) starts there too."""
    from app.models.tables import Transaction

    async def f(s):
        s.add(Transaction(subject=SUPERADMIN_EMAIL, ticker=None, txn_type="Adjust",
                          quantity=Decimal("0"), source="reconcile",
                          executed_at=NOW - timedelta(days=days_ago)))
    _db(f)


def _get(c, rng="1M", tk="QQQ"):
    return c.get(f"/api/v1/portfolio/position-history?ticker={tk}&range={rng}")


def test_cash_is_refused(client):
    c, _ = client
    r = _get(c, tk="CASH")
    assert r.status_code == 422 and "cash" in r.json()["detail"].lower()


def test_a_ticker_you_do_not_hold_is_refused(client):
    c, _ = client
    assert _get(c, tk="NVDA").status_code == 422


@pytest.mark.parametrize("rng", ["1D", "1W", "1M", "3M", "1Y", "ALL"])
def test_every_range_returns_a_price_line(client, rng):
    c, _ = client
    r = _get(c, rng)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["price"]["points"]) >= 2
    assert body["range"] == rng


def test_the_value_line_never_starts_before_the_position_existed(client):
    c, series = client
    _backdate_position(10)
    _ledger_began(40)
    body = _get(c, "3M").json()
    v = body["value"]
    assert v["clipped"] is True
    first = v["points"][0]["t"]
    assert first >= (NOW.date() - timedelta(days=10)).isoformat(), first
    assert len(body["price"]["points"]) > len(v["points"]), "price goes back further than the record"


def test_a_top_up_is_a_step_and_is_booked_as_a_trade_not_a_gain(client):
    c, series = client
    _backdate_position(20)
    _ledger_began(25)
    # 4 of today's 10 shares were bought 5 days ago.
    from app.models.tables import Transaction

    async def f(s):
        s.add(Transaction(subject=SUPERADMIN_EMAIL, ticker="QQQ", market="TASE",
                          txn_type="Buy", quantity=Decimal("4"), price=Decimal("125"),
                          value_ils=Decimal("500"), source="test",
                          executed_at=NOW - timedelta(days=5)))
    _db(f)
    v = _get(c, "1M").json()["value"]
    assert v["available"], v
    shares = [p["shares"] for p in v["points"]]
    assert shares[0] == pytest.approx(6.0) and shares[-1] == pytest.approx(10.0), shares
    # Market part = shares held x price moves; the rest is the trade.
    assert v["change_ils"] == pytest.approx(v["market_change_ils"] + v["trades_change_ils"])
    assert v["trades_change_ils"] > 0
    # Price rises 1/day: market change is 6 shares for the early days, 10 after.
    assert v["market_change_ils"] < 10 * (len(shares) - 1), "a buy was counted as a gain"


def test_an_unreconcilable_ledger_hides_the_value_line(client):
    c, _ = client
    _backdate_position(20)
    _ledger_began(25)
    from app.models.tables import Transaction

    async def f(s):
        # "Bought" 50 shares 3 days ago, but only 10 are held: before the buy the
        # count would be -40, which no book has ever held.
        s.add(Transaction(subject=SUPERADMIN_EMAIL, ticker="QQQ",
                          market="TASE", txn_type="Buy", quantity=Decimal("50"),
                          price=Decimal("120"), value_ils=Decimal("0"), source="test",
                          executed_at=NOW - timedelta(days=3)))
    _db(f)
    body = _get(c, "1M").json()
    assert body["value"]["available"] is False
    assert "missing or recorded twice" in body["value"]["reason"]
    assert len(body["price"]["points"]) >= 2, "the price line still shows"


def test_the_value_line_never_starts_before_the_ledger_did(client):
    """Opened before the ledger existed: share counts before it were never
    recorded, so the line starts at the ledger and says so."""
    c, _ = client
    _backdate_position(60)
    _ledger_began(8)
    v = _get(c, "3M").json()["value"]
    assert v["points"][0]["t"] >= (NOW.date() - timedelta(days=8)).isoformat()
    assert "ledger" in v["starts_because"]


def test_a_sale_with_no_share_count_hides_the_line_and_says_why(client):
    """Production's trims were written as 0-share sells before PR 68.1. The
    count cannot be rebuilt through them, so the line must not be drawn."""
    c, _ = client
    _backdate_position(20)
    _ledger_began(25)
    from app.models.tables import Transaction

    async def f(s):
        s.add(Transaction(subject=SUPERADMIN_EMAIL, ticker="QQQ", market="TASE",
                          txn_type="Sell", quantity=Decimal("0"), source="trim",
                          executed_at=NOW - timedelta(days=4)))
    _db(f)
    v = _get(c, "1M").json()["value"]
    assert v["available"] is False
    assert "no share count" in v["reason"]


def test_a_trade_counted_twice_is_caught_at_the_opening(client):
    """The fund_sleeve double-record: undoing both copies of a buy leaves a
    negative count before the position opened, which must hide the line."""
    c, _ = client
    _backdate_position(10)
    _ledger_began(25)
    from app.models.tables import Transaction

    async def f(s):
        for src in ("sleeve_fund", "fund_sleeve"):
            s.add(Transaction(subject=SUPERADMIN_EMAIL, ticker="QQQ", market="TASE",
                              txn_type="Buy", quantity=Decimal("6"), price=Decimal("110"),
                              value_ils=Decimal("0"), source=src,
                              executed_at=NOW - timedelta(days=10) + timedelta(seconds=1)))
    _db(f)
    v = _get(c, "1M").json()["value"]
    assert v["available"] is False and "recorded twice" in v["reason"], v


def test_all_range_still_draws_a_young_position(client, monkeypatch):
    """Downsampling ten years to 400 points must not erase the last few days."""
    c, _ = client
    long = [((date.today() - timedelta(days=2599 - i)).isoformat(), 100.0 + i * 0.01)
            for i in range(2600)]
    monkeypatch.setattr(registry, "guarded_history", lambda tk, days=252: long[-days:])
    _backdate_position(7)
    _ledger_began(30)
    v = _get(c, "ALL").json()["value"]
    assert v["available"] and len(v["points"]) >= 6, v


def test_the_chart_lists_your_buys_since_the_position_opened(client):
    """The timing detail lives here, not on the holdings list (Eran, 2026-09-21).
    Newest first, priced buys only, lots from before the position opened left out,
    and the fund_sleeve duplicates still in the ledger collapsed to one."""
    c, _ = client
    _backdate_position(10)
    _ledger_began(30)
    from app.models.tables import Transaction

    async def f(s):
        def buy(days_ago, qty, px, src="test", sec=0):
            s.add(Transaction(subject=SUPERADMIN_EMAIL, ticker="QQQ", market="TASE",
                              txn_type="Buy", quantity=Decimal(str(qty)), price=Decimal(str(px)),
                              value_ils=Decimal("0"), source=src,
                              executed_at=NOW.replace(microsecond=0) - timedelta(days=days_ago)
                              + timedelta(seconds=sec)))
        buy(20, 5, 90.0)                       # before the position opened: excluded
        buy(6, 2, 120.0)
        buy(2, 1, 125.0, "sleeve_fund")
        buy(2, 1, 125.0, "fund_sleeve")        # the old double record: collapsed
        buy(1, 1, 0)                           # unpriced: skipped
    _db(f)
    body = _get(c, "1M").json()
    buys = body["buys"]
    assert [b["price"] for b in buys] == [125.0, 120.0], buys
    cur = body["current_price"]
    assert buys[0]["change_pct"] == pytest.approx((cur / 125.0 - 1) * 100, abs=0.01)
    assert body["avg_price"] == pytest.approx(100.0)
