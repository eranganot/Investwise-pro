"""PR 147.1 -- the trades PR 147 found at price 0 / ₪0 are filled from what was measured.

Preview first, written only on `confirm`. VALUE: the row's own, else the rule event
that executed it (cash received + tax), else an estimate; PRICE: the market that
half hour. MEASURED only when the two agree within 5%.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.models.tables import RuleEvent, Transaction  # noqa: E402
from app.services import ledger_repair as lr  # noqa: E402
from app.services.feed_service import ensure_user  # noqa: E402

T0 = datetime(2026, 10, 23, 5, 45, 17, tzinfo=timezone.utc)
FX = 3.2


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


@pytest.fixture
def market(monkeypatch):
    px = {"SOXL": 82.11, "XOM": 172.0, "QQQ": 600.0}
    monkeypatch.setattr(lr, "_price_at", lambda tk, when: (px.get(tk), "30-min bar") if tk in px
                        else (None, "no price"))
    from app.services import fx
    monkeypatch.setattr(fx, "fx_rate_with_status", lambda ccy: (FX if ccy == "USD" else 1.0, "ok"))


async def _rows(s, email):
    user = await ensure_user(s, email)

    def tx(tk, side, q, price, value, src, at=T0, note=None):
        s.add(Transaction(subject=user.email, ticker=tk, market="NASDAQ", txn_type=side,
                          quantity=q, price=price, value_ils=value, tax_ils=0, source=src,
                          note=note, executed_at=at))
    # 1. the 10-23 SOXL stop: shares only; its rule event has the cash
    tx("SOXL", "Sell", 15.7829, 0, 0, "sell_position", note="card rule_c89d8251")
    s.add(RuleEvent(subject=user.email, rule_id="c89d8251", ticker="SOXL", rule_type="stop_loss",
                    outcome="executed", outcome_at=T0,
                    action={"executed": {"kind": "sell_position", "shares": 15.7829,
                                         "cash_added_ils": 4147.0, "tax_ils": 0.0}}))
    # 2. the 10-21 XOM funded buy: value, no price
    tx("XOM", "Buy", 1.6118, 0, 890.91, "buy_funded", note="card rec_e43d77")
    # 3. a drift trim with neither, and no rule event
    tx("QQQ", "Sell", 0.7207, 0, 0, "trim", note="card rec_6e22ff")
    # 4. a ticker with no price that half hour
    tx("ZZZ", "Sell", 1.0, 0, 0, "trim")
    # 5. a clean row: untouched
    tx("VTI", "Buy", 1.0, 374.9, 1270.0, "held_cash_redeploy")
    await s.commit()
    return user


@pytest.mark.asyncio
async def test_the_preview_fills_each_row_from_what_was_measured(market):
    eng, S = _session()
    try:
        async with S() as s:
            user = await _rows(s, "pr1471-a@example.com")
            got = {r["ticker"]: r for r in (await lr.preview(s, user))["unpriced_trades"]}
            assert set(got) == {"SOXL", "XOM", "QQQ", "ZZZ"}
            soxl = got["SOXL"]
            assert soxl["value_ils"] == 4147.0 and soxl["price"] == 82.11
            assert soxl["confidence"] == "measured" and "rule event" in soxl["method"]
            # 890.91 / 1.6118 / 3.2 = 172.73, within 5% of the 172.00 bar
            assert got["XOM"]["confidence"] == "measured" and got["XOM"]["price"] == 172.0
            assert got["QQQ"]["confidence"] == "estimate"
            assert got["QQQ"]["value_ils"] == pytest.approx(round(0.7207 * 600.0 * FX, 2))
            assert got["ZZZ"]["confidence"] == "unresolved" and got["ZZZ"]["price"] is None
            # read-only
            rows = (await s.execute(select(Transaction).where(Transaction.ticker == "SOXL"))).scalars().all()
            assert float(rows[0].price) == 0
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_apply_writes_measured_rows_only_unless_asked(market):
    eng, S = _session()
    try:
        async with S() as s:
            user = await _rows(s, "pr1471-b@example.com")
            assert (await lr.apply(s, user, confirm=False))["ok"] is False
            out = await lr.apply(s, user, confirm=True)
            assert {r["ticker"] for r in out["repriced"]} == {"SOXL", "XOM"}
            by = {t.ticker: t for t in (await s.execute(select(Transaction).where(
                Transaction.subject == user.email))).scalars().all()}
            assert float(by["SOXL"].price) == pytest.approx(82.11) and float(by["SOXL"].value_ils) == 4147.0
            assert "repriced 2026-10-23: measured" in by["SOXL"].note and "was price 0" in by["SOXL"].note
            assert float(by["XOM"].value_ils) == 890.91          # its own value is kept
            assert float(by["QQQ"].price) == 0                     # an estimate: not without asking
            assert float(by["VTI"].price) == 374.9 and "repriced" not in (by["VTI"].note or "")
            # idempotent: a second apply changes nothing
            again = await lr.apply(s, user, confirm=True)
            assert again["repriced"] == []
            # estimates on request
            more = await lr.apply(s, user, confirm=True, include_estimates=True)
            assert {r["ticker"] for r in more["repriced"]} == {"QQQ"}
    finally:
        await eng.dispose()
