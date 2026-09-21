"""The trade ledger must record how many shares moved -- once.

OBSERVED ON PRODUCTION, 2026-09-21 (position-history trades, range=ALL):

  * every `trim` sell is in `transactions` with quantity 0.0 -- 15 rows across
    SOXL, QQQ and BND. The trim handler returned {"sold": ["SOXL"], ...} with no
    share count, and `_record_apply` fell back to `detail.get("shares") or 0`.
  * funding a sleeve from a card wrote each leg twice at the same second:
    `sleeve_fund` (by `_execute_funded_sleeve` itself) and `fund_sleeve` (by
    `_record_apply`) -- SOXL +3.9046 x2, +6.2778 x2; BND -6.7875 x2, -4.4084 x2.

WHY SILENT: `record_leg` accepts any quantity and nothing reconciled the
ledger against the positions it describes. The first reader of share counts
was the PR 68 holding chart, which is how it surfaced.

Not financial advice.
"""
import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "SOXL", "market": "TASE", "asset_class": "Equities", "depth": 3,
         "spot_price": 130.0, "listing_price": 130.0, "quantity": 20.0,
         "cost_basis": 110.0, "expected_return_pct": 7, "volatility_pct": 60}]})
    c.post("/api/v1/portfolio/cash", json={"amount_ils": 500.0})


@pytest.mark.asyncio
async def test_a_trim_records_the_shares_it_sold(own_session, monkeypatch):
    from sqlalchemy import select

    import app.services.recommendations as rr
    from app.models.tables import Transaction, User

    card = {"id": "rec_trim01", "title": "Trim SOXL", "dimension": "holding",
            "apply": {"kind": "trim", "ticker": "SOXL", "shares": 4.5}}

    async def _built(session, user):
        return {"recommendations": [card]}

    monkeypatch.setattr(rr, "build_recommendations", _built)
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            async with own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                out = await rr.apply_recommendation(s, user, "rec_trim01")
                rows = (await s.execute(select(Transaction).where(
                    Transaction.ticker == "SOXL"))).scalars().all()
    finally:
        logging.disable(logging.NOTSET)
    assert out and out.get("applied") == "trim", out
    sells = [r for r in rows if r.txn_type == "Sell"]
    assert len(sells) == 1, [(r.txn_type, float(r.quantity)) for r in rows]
    assert float(sells[0].quantity) == pytest.approx(4.5), "a trim was recorded as 0 shares"


@pytest.mark.asyncio
async def test_a_handler_that_recorded_its_own_legs_is_not_recorded_again(own_session):
    from sqlalchemy import select

    from app.models.tables import Transaction, User
    from app.services import ledger_service as ledger
    from app.services.recommendations import _record_apply

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            async with own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                bought = [{"ticker": "SOXL", "shares": 3.9046, "price": 123.77,
                           "amount_ils": 1700.0}]
                # What `_execute_funded_sleeve` does, then returns.
                await ledger.record_legs(s, user, sold=[], bought=bought,
                                         source="sleeve_fund", note="x")
                await s.commit()
                detail = {"sold": [], "bought": bought, "ledger_recorded": True}
                await _record_apply(s, user, "fund_sleeve", "rec_fund01", detail)
                rows = (await s.execute(select(Transaction).where(
                    Transaction.ticker == "SOXL"))).scalars().all()
    finally:
        logging.disable(logging.NOTSET)
    assert [(r.source, float(r.quantity)) for r in rows] == [("sleeve_fund", pytest.approx(3.9046))]


def test_the_funded_sleeve_executor_declares_it_recorded_its_legs():
    """The contract the skip depends on, held at its source."""
    import inspect

    from app.services import strategy_service as ss
    src = inspect.getsource(ss._execute_funded_sleeve)
    assert "ledger.record_legs(" in src
    assert '"ledger_recorded": True' in src
