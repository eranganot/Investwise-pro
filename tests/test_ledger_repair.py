"""The historical ledger repair: preview first, write only what can be defended.

The book below reproduces both production defects in one holding:
  * a sleeve buy recorded twice (sleeve_fund + fund_sleeve, same second);
  * two trims recorded as 0-share sells, each naming its card.
Opened inside the ledger with a recorded opening buy, so the books give the
EXACT total the two trims sold: 25 + 4 - 24 = 5 shares.

Not financial advice.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import app.providers.registry as registry
from app.main import app
from app.services.feed_service import SUPERADMIN_EMAIL

NOW = datetime.now(timezone.utc).replace(microsecond=0)
OPENED = NOW - timedelta(days=10)


def _db(fn):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings

    async def _run():
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                out = await fn(s)
                await s.commit()
                return out
        finally:
            await engine.dispose()
    return asyncio.run(_run())


def _tx(**kw):
    from app.models.tables import Transaction
    base = dict(subject=SUPERADMIN_EMAIL, ticker="SOXL", market="TASE", price=Decimal("0"),
                value_ils=Decimal("0"), tax_ils=Decimal("0"))
    base.update(kw)
    return Transaction(**base)


@pytest.fixture()
def client(monkeypatch):
    # Every price is 100 (ILS, TASE -> FX 1.0), so a ₪ amount / 100 = shares.
    bars = [((NOW - timedelta(days=30) + timedelta(minutes=30 * i)).isoformat(), 100.0)
            for i in range(30 * 48)]
    monkeypatch.setattr(registry, "guarded_intraday", lambda tk, span="1d": bars)
    daily = [((NOW.date() - timedelta(days=29 - i)).isoformat(), 100.0) for i in range(30)]
    monkeypatch.setattr(registry, "guarded_history", lambda tk, days=252: daily[-days:])
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "SOXL", "market": "TASE", "asset_class": "Equities", "depth": 2,
             "spot_price": 100.0, "listing_price": 100.0, "quantity": 24, "cost_basis": 100.0,
             "expected_return_pct": 8, "volatility_pct": 60}]})

        async def seed(s):
            from sqlalchemy import update

            from app.models.tables import KVSetting, Position
            await s.execute(update(Position).where(Position.ticker == "SOXL")
                            .values(created_at=OPENED))
            s.add(_tx(ticker=None, txn_type="Adjust", quantity=Decimal("0"), source="reconcile",
                      executed_at=OPENED - timedelta(days=5)))                 # ledger begins
            s.add(_tx(txn_type="Buy", quantity=Decimal("25"), price=Decimal("100"),
                      source="sleeve_fund", executed_at=OPENED + timedelta(seconds=2)))
            for src in ("sleeve_fund", "fund_sleeve"):                         # the double record
                s.add(_tx(txn_type="Buy", quantity=Decimal("4"), price=Decimal("110"),
                          source=src, executed_at=NOW - timedelta(days=6)))
            s.add(_tx(txn_type="Sell", quantity=Decimal("0"), source="trim", note="card rec_a",
                      executed_at=NOW - timedelta(days=4)))
            s.add(_tx(txn_type="Sell", quantity=Decimal("0"), source="trim", note="card rec_b",
                      executed_at=NOW - timedelta(days=2)))
            # The cards' own ₪ amounts: 3.3 and 1.8 shares at 100 -- 5.1 against the
            # books' exact 5.0, so the split is scaled by 0.980.
            s.add(KVSetting(key=f"completed_recs:{SUPERADMIN_EMAIL}", value=json.dumps({
                "rec_a": {"at": NOW.isoformat(), "amount": 330.0},
                "rec_b": {"at": NOW.isoformat(), "amount": 180.0}})))
        _db(seed)
        yield c


def _rows():
    async def q(s):
        from sqlalchemy import select

        from app.models.tables import Transaction
        return [(t.txn_type, float(t.quantity), t.source, t.note or "")
                for t in (await s.execute(select(Transaction).where(Transaction.ticker == "SOXL")
                          .order_by(Transaction.executed_at))).scalars().all()]
    return _db(q)


def test_the_preview_finds_both_defects_and_writes_nothing(client):
    before = _rows()
    p = client.get("/api/v1/portfolio/ledger/repair").json()
    assert p["read_only"] and p["summary"]["duplicates"] == 1
    assert p["duplicates"][0]["side"] == "Buy" and p["duplicates"][0]["shares"] == 4.0
    bs = p["blind_sells"]
    assert [b["confidence"] for b in bs] == ["balanced", "balanced"], bs
    assert sum(b["shares"] for b in bs) == pytest.approx(5.0, abs=1e-6)
    assert bs[0]["shares"] == pytest.approx(3.3 * 5.0 / 5.1, abs=1e-4)
    ep = next(e for e in p["episodes"] if e["ticker"] == "SOXL")
    assert ep["anchored"] and ep["exact_total_sold"] == pytest.approx(5.0)
    assert _rows() == before, "the preview changed the ledger"


def test_apply_requires_confirm(client):
    r = client.post("/api/v1/portfolio/ledger/repair", json={})
    assert r.status_code == 422
    assert all(q != 0.0 or t != "Sell" or "repaired" not in n for t, q, _s, n in _rows())


def test_apply_voids_the_duplicate_sizes_the_trims_and_the_chart_reconciles(client):
    r = client.post("/api/v1/portfolio/ledger/repair", json={"confirm": True}).json()
    assert r["ok"] and len(r["voided"]) == 1 and len(r["written"]) == 2
    rows = _rows()
    voids = [x for x in rows if x[0] == "Void"]
    assert len(voids) == 1 and voids[0][2] == "fund_sleeve" and "was Buy" in voids[0][3]
    sells = [x for x in rows if x[0] == "Sell"]
    assert sum(q for _t, q, _s, _n in sells) == pytest.approx(5.0, abs=1e-3)
    assert all("was 0 shares" in n for *_x, n in sells)
    # Nothing was deleted.
    assert len(rows) == 5
    # The holding chart's own reconciliation now passes.
    v = client.get("/api/v1/portfolio/position-history?ticker=SOXL&range=1M").json()["value"]
    assert v["available"], v.get("reason")


def test_a_second_apply_changes_nothing(client):
    client.post("/api/v1/portfolio/ledger/repair", json={"confirm": True})
    once = _rows()
    r = client.post("/api/v1/portfolio/ledger/repair", json={"confirm": True}).json()
    assert r["voided"] == [] and r["written"] == []
    assert _rows() == once


def test_an_estimate_without_the_books_is_not_written_by_default(client):
    """A blind sell outside any anchored episode has only its card's estimate."""
    async def add(s):
        s.add(_tx(ticker="QQQ", txn_type="Sell", quantity=Decimal("0"), source="trim",
                  note="card rec_a", executed_at=NOW - timedelta(days=3)))
    _db(add)
    p = client.get("/api/v1/portfolio/ledger/repair").json()
    q = next(b for b in p["blind_sells"] if b["ticker"] == "QQQ")
    assert q["confidence"] == "estimate"
    r = client.post("/api/v1/portfolio/ledger/repair", json={"confirm": True}).json()
    assert any(s["ticker"] == "QQQ" for s in r["skipped"])
