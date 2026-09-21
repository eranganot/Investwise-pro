"""The historical ledger repair: preview first, write only what can be defended.

The book below reproduces both production defects in one holding:
  * a sleeve buy recorded twice (sleeve_fund + fund_sleeve, same second);
  * two trims recorded as 0-share sells, each naming its card -- one fired by a
    rule (its event holds the cash actually received: MEASURED), one not (only
    the card's own pre-trade amount: an ESTIMATE).
The books (25 + 4 - 24 = 5 sold) are reported as a check and never used to
size anything: on production they were short by trades another path never
recorded (see ledger_repair's docstring).

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
            # rec_a was fired by a rule: 300 cash + 10 tax = 310 -> 3.1 shares at 100.
            from app.models.tables import RuleEvent
            s.add(RuleEvent(subject=SUPERADMIN_EMAIL, rule_id="r1", ticker="SOXL",
                            rule_type="max_weight", outcome="executed",
                            outcome_at=NOW - timedelta(days=4) + timedelta(seconds=20),
                            action={"plan": {"kind": "trim", "shares": 3.05},
                                    "executed": {"kind": "trim", "cash_added_ils": 300.0,
                                                 "tax_ils": 10.0}}))
            # The cards' own ₪ amounts: 330 (rec_a, superseded by its rule event)
            # and 180 (rec_b -> 1.8 shares, an estimate).
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
    assert [b["confidence"] for b in bs] == ["measured", "estimate"], bs
    assert bs[0]["shares"] == pytest.approx(3.1) and bs[1]["shares"] == pytest.approx(1.8)
    ep = next(e for e in p["episodes"] if e["ticker"] == "SOXL")
    assert ep["books_say_sold"] == pytest.approx(5.0)
    assert ep["repair_would_size"] == pytest.approx(4.9), "the books are a check, not a source"
    assert _rows() == before, "the preview changed the ledger"


def test_apply_requires_confirm(client):
    before = _rows()
    assert client.post("/api/v1/portfolio/ledger/repair", json={}).status_code == 422
    assert _rows() == before


def test_apply_voids_the_duplicate_and_writes_only_the_measured_size(client):
    r = client.post("/api/v1/portfolio/ledger/repair", json={"confirm": True}).json()
    assert r["ok"] and len(r["voided"]) == 1
    assert [w["confidence"] for w in r["written"]] == ["measured"]
    assert [x["confidence"] for x in r["skipped"]] == ["estimate"]
    rows = _rows()
    voids = [x for x in rows if x[0] == "Void"]
    assert len(voids) == 1 and voids[0][2] == "fund_sleeve" and "was Buy" in voids[0][3]
    sells = sorted(q for t, q, _s, _n in rows if t == "Sell")
    assert sells == [0.0, pytest.approx(3.1)]
    assert len(rows) == 5, "nothing is deleted"


def test_estimates_are_written_only_when_asked(client):
    r = client.post("/api/v1/portfolio/ledger/repair",
                    json={"confirm": True, "include_estimates": True}).json()
    assert sorted(w["confidence"] for w in r["written"]) == ["estimate", "measured"]
    rows = [x for x in _rows() if x[0] == "Sell"]
    assert all("was 0 shares" in n for *_x, n in rows)


def test_a_second_apply_changes_nothing(client):
    client.post("/api/v1/portfolio/ledger/repair", json={"confirm": True})
    once = _rows()
    r = client.post("/api/v1/portfolio/ledger/repair", json={"confirm": True}).json()
    assert r["voided"] == [] and r["written"] == []
    assert _rows() == once


def test_one_rule_event_sizes_one_trim(client):
    """Production: one 15:43:15 event was matched to trims at 15:42 AND 15:43."""
    async def add(s):
        s.add(_tx(txn_type="Sell", quantity=Decimal("0"), source="trim", note="card rec_z",
                  executed_at=NOW - timedelta(days=4) + timedelta(seconds=60)))
    _db(add)
    p = client.get("/api/v1/portfolio/ledger/repair").json()
    measured = [b for b in p["blind_sells"] if b["confidence"] == "measured"]
    assert len(measured) == 1, measured
