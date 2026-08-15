"""Backlog items 7-10, each one found by looking at something live.

The theme: a number that is technically correct and not worth acting on, or a
guard that protects one door and not the next.
"""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.schemas.intake import IntakePosition
from app.schemas.market import Quote
from app.schemas.state_machine import Market
from app.services import pricing_service as ps
from app.services import rules_service as rs
from app.services.feed_service import ensure_user
from app.services.funding_service import MIN_TRADE_ILS
from app.services.intake_service import (
    ensure_account, ensure_entity, get_cash, list_positions, upsert_positions)


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


# --------------------------------------------------------------------------- #
# 7 — a cap has no tolerance band, so it fires on trivial overshoots
# --------------------------------------------------------------------------- #
def _cap(level=20.0):
    return NS(rule_type="max_weight", level=level, mode="pct", ticker="MSFT")


def test_a_trivial_cap_breach_produces_no_trade():
    """Live: MSFT sat 0.2 points over a 20% cap, which is a ~43 shekel trim
    against a 250 minimum. Technically a breach; not worth the friction, and the
    app refuses trades that small everywhere else."""
    pos = {"qty": 2.8243, "weight_pct": 20.2, "price": 510.88, "value_ils": 4329.0}
    assert rs.execution_plan(_cap(), pos) is None


def test_a_real_cap_breach_still_produces_a_trim():
    """The guard must not swallow a breach worth acting on."""
    pos = {"qty": 60.0, "weight_pct": 40.0, "price": 100.0, "value_ils": 8000.0}
    plan = rs.execution_plan(_cap(), pos)
    assert plan and plan["kind"] == "trim"
    # 40% -> 20% of an 8,000 position is ~4,000, comfortably over the minimum.
    assert plan["shares"] > 0


def test_the_trim_threshold_is_the_apps_own_minimum():
    """Sized against MIN_TRADE_ILS rather than a number invented here."""
    over = 0.6                                  # 20.6% against a 20% cap
    value = MIN_TRADE_ILS / (over / 20.6) - 1   # just under the minimum
    pos = {"qty": 10.0, "weight_pct": 20.6, "price": 10.0, "value_ils": value}
    assert rs.execution_plan(_cap(), pos) is None
    pos["value_ils"] = value * 3                # comfortably over
    assert rs.execution_plan(_cap(), pos) is not None


def test_the_index_carries_an_ils_value_not_a_native_one():
    """The trim threshold is in shekels, so the value it reads must be too."""
    import inspect
    src = inspect.getsource(rs._positions_index)
    assert '"value_ils"' in src
    assert "snap.get(\"nav\")" in src, "value must come from the FX-normalised snapshot"


# --------------------------------------------------------------------------- #
# 8 — upsert_positions was the last unguarded door to the cash row
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_upsert_cannot_misprice_the_cash_row():
    """No caller passes CASH today. The guard exists so that "no caller does"
    stops being the thing keeping the balance correct."""
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await ensure_user(s, "upsert_cash_guard@example.com")
            entity = await ensure_entity(s, user, "Personal", "Personal")
            account = await ensure_account(s, entity, "Main")
            await s.commit()
            # Exactly the shape that corrupted the live book, via a third door.
            await upsert_positions(s, account, [IntakePosition(
                ticker="CASH", market=Market.NASDAQ, depth=1, spot_price=86.85,
                listing_price=86.85, quantity=642.19, cost_basis=86.85,
                asset_class="Cash")])
            await s.commit()
            rows = {p.ticker.upper(): p for p in await list_positions(s, user)}
            balance = await get_cash(s, user)
    finally:
        await eng.dispose()

    assert float(rows["CASH"].current_price) == 1.0
    assert float(rows["CASH"].cost_basis) == 1.0
    assert rows["CASH"].meta.get("price_currency") == "ILS"
    assert balance == pytest.approx(642.19), "the quantity IS the balance"


# --------------------------------------------------------------------------- #
# 9 — a primary with no venue timestamp must be visible, not silent
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_primary_without_timestamps_is_counted(monkeypatch):
    """Production runs Yahoo, so the FMP path is never exercised live. If the
    primary is ever switched and supplies no venue timestamp, the whole
    staleness guard silently rests on Yahoo being reachable. Count it so that
    shows up in the response and the log instead of being invisible."""
    monkeypatch.setattr(ps, "market_provider", lambda: NS(name="fmp"))
    monkeypatch.setattr(ps, "guarded_quote", lambda tk: Quote(
        ticker=tk, market="US", price=100.0, currency="USD",
        as_of="2026-08-10T12:00:00+00:00", as_of_source="request"))
    monkeypatch.setattr(ps, "YahooMarketDataProvider", lambda: NS(
        name="yahoo", get_quote=lambda tk: Quote(
            ticker=tk, market="US", price=100.0, currency="USD",
            as_of="2026-08-10T12:00:00+00:00", as_of_source="market")))

    eng, Session = _session()
    try:
        async with Session() as s:
            user = await ensure_user(s, "fmp_probe@example.com")
            entity = await ensure_entity(s, user, "Personal", "Personal")
            account = await ensure_account(s, entity, "Main")
            await upsert_positions(s, account, [IntakePosition(
                ticker="MSFT", market=Market.NASDAQ, depth=1, spot_price=100.0,
                listing_price=100.0, quantity=5, cost_basis=100.0,
                asset_class="Equities")])
            await s.commit()
            res = await ps.refresh_all_positions(s, positions=await list_positions(s, user))
    finally:
        await eng.dispose()

    assert res["unknown_from_primary"] == 1
    assert res["updated"] == 1, "the cross-check still lets a fresh quote through"


def test_fmp_reads_a_real_timestamp_when_one_is_present():
    """The field name came from the docs, never from an observed response. Pin
    both shapes so a change is a test failure rather than a silent fallback."""
    from app.providers.live import FMPMarketDataProvider as F

    p = F.__new__(F)
    monkey = {"with_ts": [{"price": 101.5, "exchange": "NASDAQ",
                           "timestamp": 1754827200}],
              "no_ts": [{"price": 101.5, "exchange": "NASDAQ"}]}
    p._get = lambda *a, **k: monkey["with_ts"]          # noqa: SLF001
    q = p.get_quote("MSFT")
    assert q.as_of_source == "market"
    assert q.as_of.startswith("2025-08-10") or q.as_of.startswith("2025-08-11")

    p._get = lambda *a, **k: monkey["no_ts"]            # noqa: SLF001
    q = p.get_quote("MSFT")
    assert q.as_of_source == "request", "no timestamp must never claim to be one"


# --------------------------------------------------------------------------- #
# 10 — the redeploy card must not vanish because every leg is too small
# --------------------------------------------------------------------------- #
def test_the_redeploy_card_concentrates_rather_than_disappearing():
    """Live: NAV 22,006, spendable 1,929, the equities share split four ways at
    ~138 each -- all under the 250 minimum, so no legs and no card, while the
    cash sat idle and the app reported nothing to do.

    One trade that clears the minimum beats four that do not."""
    import inspect

    from app.services import recommendations as rr
    src = inspect.getsource(rr._redeploy_cash_recs)
    assert "concentrates it into one trade" in src
    assert "if not legs and remaining >= _fund.MIN_TRADE_ILS" in src
    # It must still respect the single-name cap when it concentrates.
    tail = src[src.index("if not legs and remaining"):]
    # `size_purchase` split into class_gap_ils (what the plan wants) and
    # name_room_ils (the cap ceiling). The concentrated leg wants the ceiling.
    assert "name_room_ils" in tail, "the concentrated leg must still be capped"
