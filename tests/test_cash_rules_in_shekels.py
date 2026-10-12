"""Cash rules in shekels, and "vs your last buy".

Eran, 2026-09-21:
  "the card should appear if I have more than 1500, not by % of my portfolio"
  -> chosen: above 1,500 the card appears and invests down to the 150 floor.
  "the % should represent diff from the last purchase or diff from avg price"
  -> the headline stays vs average price; the last buy is shown beside it.

Observed the day it was asked: cash 1,661.12 on a NAV of 23,057.90 -- 7.2%,
UNDER the 7.5% target (1,729.34), so `spendable_cash` was 0 and no card could
exist. Nothing was broken; the rule was a percentage and he wanted a number.

Not financial advice.
"""
from __future__ import annotations

import os
import types
from datetime import datetime, timedelta, timezone
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import funding_service as fund  # noqa: E402
from app.services.recommendations import _redeploy_cash_recs  # noqa: E402


class _Row:
    def __init__(self, ticker, value_ils, asset_class="Equities"):
        self.ticker, self.market = ticker, "TASE"
        self.quantity, self.current_price, self.cost_basis = float(value_ils), 1.0, 1.0
        self.meta = {"asset_class": asset_class, "price_currency": "ILS"}


PLAN = types.SimpleNamespace(cash_target_pct=None, cash_target_ils=150, cash_redeploy_at_ils=1500,
                             currency="ILS", objective="Grow", risk_tolerance="High")
TARGET = {"Equities": 0.75, "Fixed Income": 0.20, "Cash": 0.05}


def _book(cash):
    rows = [_Row("CASH", cash, "Cash"), _Row("VTI", 15000.0), _Row("BND", 3000.0, "Fixed Income")]
    return rows, sum(r.quantity for r in rows)


def _cards(cash):
    rows, nav = _book(cash)
    return _redeploy_cash_recs(rows, {"nav": nav, "exposure_ticker": {}}, PLAN, "Grow",
                               0.45, cash, TARGET, None, None)


def test_the_target_in_shekels_is_the_same_number_to_every_reader():
    assert fund.cash_target_amount_ils(23057.9, "Grow", PLAN) == 150.0
    assert fund.cash_target_fraction("Grow", PLAN, 23057.9) == pytest.approx(150.0 / 23057.9)
    assert fund.cash_soft_floor_ils(23057.9, "Grow", PLAN) == 150.0


def test_above_the_trigger_the_card_invests_down_to_the_floor():
    card = _cards(1661.12)
    assert card, "cash is above 1,500 -- there must be a card"
    deployed = sum(x["amount_ils"] for x in card[0]["apply"]["legs"])
    assert deployed == pytest.approx(1661.12 - 150.0, abs=fund.MIN_TRADE_ILS)
    assert card[0]["meta"]["cash_after_ils"] >= 150.0 - 0.01
    assert "1,500" in card[0]["why"] and "150" in card[0]["why"]


def test_at_or_below_the_trigger_there_is_no_card():
    assert _cards(1500.0) == []
    assert _cards(1461.0) == []


def test_without_a_trigger_the_old_rule_stands():
    rows, nav = _book(1661.12)
    plan = types.SimpleNamespace(cash_target_pct=7.5, cash_target_ils=None,
                                 cash_redeploy_at_ils=None, currency="ILS", objective="Grow")
    # 7.5% of ~19,661 is ~1,475: spendable ~187, under the minimum -> no card.
    assert _redeploy_cash_recs(rows, {"nav": nav, "exposure_ticker": {}}, plan, "Grow",
                               0.45, 1661.12, TARGET, None, None) == []


@pytest.fixture()
def client():
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "AAA", "market": "TASE", "asset_class": "Equities", "depth": 2,
             "spot_price": 100.0, "listing_price": 100.0, "quantity": 200, "cost_basis": 90.0,
             "expected_return_pct": 8, "volatility_pct": 18}]})
        c.post("/api/v1/portfolio/cash", json={"amount_ils": 1661.12, "mode": "set"})
        yield c


def test_the_plan_stores_one_cash_target_at_a_time(client):
    r = client.put("/api/v1/plan", json={"objective": "Grow", "cash_target_pct": 7.5}).json()
    assert r["cash_target_pct"] == 7.5 and r["cash_target_ils"] is None
    r = client.put("/api/v1/plan", json={"cash_target_ils": 150, "cash_redeploy_at_ils": 1500}).json()
    assert r["cash_target_ils"] == 150.0 and r["cash_redeploy_at_ils"] == 1500.0
    assert r["cash_target_pct"] is None, "a shekel target clears the percentage"
    r = client.put("/api/v1/plan", json={"cash_target_pct": 5}).json()
    assert r["cash_target_ils"] is None and r["cash_target_pct"] == 5


def test_the_book_target_uses_the_shekel_amount(client):
    client.put("/api/v1/plan", json={"objective": "Grow", "cash_target_ils": 150,
                                     "cash_redeploy_at_ils": 1500})
    mix = client.get("/api/v1/mix").json()
    assert mix["target_allocation"].get("Cash", 0) == pytest.approx(150.0 / mix["nav"], abs=1e-3)


def test_a_book_at_its_floor_is_not_told_to_raise_cash(client):
    client.put("/api/v1/plan", json={"objective": "Grow", "cash_target_ils": 150,
                                     "cash_redeploy_at_ils": 1500})
    client.post("/api/v1/portfolio/cash", json={"amount_ils": 160.0, "mode": "set"})
    cards = client.get("/api/v1/recommendations").json().get("recommendations", [])
    assert not [c for c in cards if c.get("dimension") == "liquidity"], \
        [c["title"] for c in cards]


def test_each_holding_carries_its_last_buy(client):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings
    from app.models.tables import Transaction
    from app.services.feed_service import SUPERADMIN_EMAIL

    async def _add():
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine)() as s:
                now = datetime.now(timezone.utc)
                for px, ago in ((80.0, 1), (95.0, 0)):   # the later one wins
                    s.add(Transaction(subject=SUPERADMIN_EMAIL, ticker="AAA", market="TASE",
                                      txn_type="Buy", quantity=Decimal("1"),
                                      price=Decimal(str(px)), source="test",
                                      executed_at=now - timedelta(minutes=ago) + timedelta(seconds=90)))
                await s.commit()
        finally:
            await engine.dispose()
    asyncio.run(_add())
    row = next(p for p in client.get("/api/v1/portfolio").json()["positions"] if p["ticker"] == "AAA")
    lb = row["last_buy"]
    assert lb and lb["price"] == pytest.approx(95.0)
    assert lb["change_pct"] == pytest.approx((row["current_price"] / 95.0 - 1) * 100, abs=0.01)
