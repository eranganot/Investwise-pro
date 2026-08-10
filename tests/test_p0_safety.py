"""P0 safety batch — the four ways the app could quietly mislead you.

Each test here maps to one failure that was live in production:

* P0.1  "Load this basket" deleted every holding, including the ones the sleeve
        was supposed to sit alongside.
* P0.2  covered by the smoke script (presentation only).
* P0.3  a delisted holding's frozen price was accepted as current, so a
        14-month-old number was counted in NAV and in everything sized from it.
* P0.4  suggested protective rules could never reach the Today screen.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.schemas.intake import IntakePosition
from app.schemas.market import Quote
from app.schemas.state_machine import Market
from app.services import pricing_service as ps
from app.services import strategy_service as ss
from app.services.feed_service import ensure_user
from app.services.intake_service import (
    ensure_account, ensure_entity, get_cash, list_positions, set_cash,
    upsert_positions)

TREND = "btm_trend_tqqq"          # aggressive TQQQ, core QQQ, suggested sleeve 20%


def _session():
    """Throwaway NullPool engine in THIS test's event loop.

    Borrowing the app's shared async engine across loops makes asyncpg reject the
    connection ('attached to a different loop'); SQLite tolerates it and Postgres
    does not, so the CI Postgres job is the only place it shows up.
    """
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


async def _book(s, email, positions):
    user = await ensure_user(s, email)
    entity = await ensure_entity(s, user, "Personal", "Personal")
    account = await ensure_account(s, entity, "Main")
    await upsert_positions(s, account, positions)
    await s.commit()
    return user


def _pos(ticker, qty=100, price=100.0, basis=100.0, market=Market.TASE):
    return IntakePosition(ticker=ticker, market=market, depth=2, spot_price=price,
                          listing_price=price, quantity=qty, cost_basis=basis,
                          asset_class="Equities")


# --------------------------------------------------------------------------- #
# P0.3 — freshness is three states, not two
# --------------------------------------------------------------------------- #
def test_trading_days_ignore_the_weekend():
    fri = datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc)     # Friday close
    mon = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)     # Monday morning
    assert ps.trading_days_between(fri, mon) == 1              # not 3
    sat = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    sun = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    assert ps.trading_days_between(sat, sun) == 0


def _q(as_of, source="market", price=100.0):
    return Quote(ticker="X", market="US", price=price, currency="USD",
                 as_of=as_of.isoformat() if hasattr(as_of, "isoformat") else as_of,
                 as_of_source=source)


def test_a_quote_from_last_friday_is_fresh_on_monday():
    now = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    state, _as_of, aged = ps.quote_freshness(_q(now - timedelta(days=3)), now)
    assert state == "fresh" and aged <= ps.STALE_AFTER_TRADING_DAYS


def test_a_quote_that_has_not_traded_for_months_is_stale():
    now = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    state, as_of, aged = ps.quote_freshness(_q(datetime(2025, 6, 11, tzinfo=timezone.utc)), now)
    assert state == "stale"
    assert as_of.startswith("2025-06-11") and aged > 200


def test_a_provider_without_a_trade_time_yields_unknown_not_fresh():
    """The original bug in one line.

    A provider that stamps 'now' on every quote makes a delisted instrument look
    like it traded a second ago. 'We cannot tell' must not collapse into 'fresh'.
    """
    now = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    state, as_of, aged = ps.quote_freshness(_q(now, source="request"), now)
    assert state == "unknown" and as_of is None and aged is None


@pytest.mark.asyncio
async def test_a_stale_quote_does_not_overwrite_the_price_and_flags_the_position(monkeypatch):
    stale_at = datetime.now(timezone.utc) - timedelta(days=400)
    monkeypatch.setattr(ps, "market_provider", lambda: NS(name="yahoo"))
    monkeypatch.setattr(ps, "guarded_quote",
                        lambda tk: _q(stale_at, "market", price=39.87))

    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "stale_probe@example.com", [_pos("COW", 10, 210.9)])
            res = await ps.refresh_all_positions(s)
            rows = {p.ticker: p for p in await list_positions(s, user)}
    finally:
        await eng.dispose()

    assert res["stale"] == 1
    assert [x["ticker"] for x in res["stale_tickers"]] == ["COW"]
    # The frozen quote is NOT written in as a current price.
    assert float(rows["COW"].current_price) == pytest.approx(210.9)
    assert rows["COW"].meta["price_stale"] is True
    assert rows["COW"].meta["price_freshness"] == "stale"
    assert rows["COW"].meta["price_as_of"].startswith(stale_at.strftime("%Y-%m-%d"))


@pytest.mark.asyncio
async def test_a_fresh_quote_clears_the_stale_flag(monkeypatch):
    monkeypatch.setattr(ps, "market_provider", lambda: NS(name="yahoo"))
    monkeypatch.setattr(ps, "guarded_quote",
                        lambda tk: _q(datetime.now(timezone.utc), "market", price=51.0))

    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "unstale_probe@example.com", [_pos("REVIVED", 10, 42.0)])
            rows = await list_positions(s, user)
            rows[0].meta = {**(rows[0].meta or {}), "price_stale": True,
                            "price_freshness": "stale", "price_stale_days": 300}
            await s.commit()
            res = await ps.refresh_all_positions(s)
            rows = {p.ticker: p for p in await list_positions(s, user)}
    finally:
        await eng.dispose()

    assert res["stale"] == 0 and res["updated"] == 1
    assert float(rows["REVIVED"].current_price) == pytest.approx(51.0)
    assert "price_stale" not in rows["REVIVED"].meta
    assert rows["REVIVED"].meta["price_freshness"] == "fresh"


@pytest.mark.asyncio
async def test_a_primary_with_no_trade_time_is_cross_checked_against_yahoo(monkeypatch):
    """FMP stamps request time, so on its own the check could never fire."""
    stale_at = datetime.now(timezone.utc) - timedelta(days=400)
    monkeypatch.setattr(ps, "market_provider", lambda: NS(name="fmp"))
    monkeypatch.setattr(ps, "guarded_quote",
                        lambda tk: _q(datetime.now(timezone.utc), "request", price=39.87))
    monkeypatch.setattr(ps, "YahooMarketDataProvider",
                        lambda: NS(name="yahoo",
                                   get_quote=lambda tk: _q(stale_at, "market", price=39.87)))

    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "crosscheck_probe@example.com", [_pos("DEAD", 10, 210.9)])
            res = await ps.refresh_all_positions(s)
            rows = {p.ticker: p for p in await list_positions(s, user)}
    finally:
        await eng.dispose()

    assert res["stale"] == 1
    assert rows["DEAD"].meta["price_stale"] is True
    assert float(rows["DEAD"].current_price) == pytest.approx(210.9)


@pytest.mark.asyncio
async def test_the_manual_refresh_endpoint_shares_the_guarded_implementation(monkeypatch):
    """Found live, by the smoke run, on the very first production check.

    POST /portfolio/refresh-prices carried its own copy of the reprice loop, and
    the copy had NEITHER the cash guard NOR the freshness check. So a manual
    refresh:
      * repriced the synthetic CASH row against NASDAQ:CASH (Pathward Financial,
        ~$73) and stamped it USD -- the phase-1 bug, which was only ever fixed in
        the scheduled job; and
      * wrote a delisted holding's 425-day-old price back in as current.
    Two implementations of one job means a fix lands on whichever one you call.
    """
    stale_at = datetime.now(timezone.utc) - timedelta(days=400)

    def _quote(tk):
        if tk.upper() == "CASH":          # the real listed ticker, not your shekels
            return Quote(ticker="CASH", market="NASDAQ", price=73.4, currency="USD",
                         as_of=datetime.now(timezone.utc).isoformat(),
                         as_of_source="market")
        if tk.upper() == "COW":
            return _q(stale_at, "market", price=39.87)
        return _q(datetime.now(timezone.utc), "market", price=50.0)

    monkeypatch.setattr(ps, "market_provider", lambda: NS(name="yahoo"))
    monkeypatch.setattr(ps, "guarded_quote", _quote)

    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "manual_refresh_probe@example.com",
                               [_pos("COW", 30, 210.9), _pos("MSFT", 10, 50.0)])
            await set_cash(s, user, 640.0)
            rows = await list_positions(s, user)
            res = await ps.refresh_all_positions(s, positions=rows)
            after = {p.ticker: p for p in await list_positions(s, user)}
            cash_after = await get_cash(s, user)
    finally:
        await eng.dispose()

    # Cash is ILS-native: 1 unit = 1 shekel, never quoted, never FX-converted.
    assert res["skipped_cash"] == 1
    assert cash_after == pytest.approx(640.0)
    assert float(after["CASH"].current_price) == 1.0
    assert after["CASH"].meta.get("price_currency") == "ILS"

    # And the delisted holding is still not accepted as current.
    assert res["stale"] == 1
    assert float(after["COW"].current_price) == pytest.approx(210.9)
    assert after["COW"].meta["price_stale"] is True

    # The response tells the caller what it refused to write.
    assert [x["ticker"] for x in res["stale_tickers"]] == ["COW"]
    assert res["prices"] and res["errors"] == []


# --------------------------------------------------------------------------- #
# P0.1 — a sleeve coexists with the book; only "replace" may destroy it
# --------------------------------------------------------------------------- #
def test_sleeve_targets_are_the_aggressive_leg_only():
    t = ss.sleeve_targets(TREND, 20)
    assert t == {"TQQQ": pytest.approx(0.20)}      # the QQQ core is NOT bought
    assert ss.sleeve_targets(TREND, 90)["TQQQ"] == pytest.approx(0.90)
    # No sleeve set -> the catalog's own suggestion, never 100%.
    assert ss.sleeve_targets(TREND)["TQQQ"] == pytest.approx(0.20)


def test_fund_is_the_default_for_a_rule_based_strategy():
    """A sleeve defaults to funding; anything else defaults to the old behaviour,
    so the destructive path is never the one you get without asking for it."""
    assert ss._default_mode(TREND) == ss.FUND
    assert ss._default_mode("not_a_strategy") == ss.REPLACE


@pytest.mark.asyncio
async def test_funding_a_sleeve_leaves_every_other_holding_alone(monkeypatch):
    monkeypatch.setattr(ss, "guarded_quote",
                        lambda tk: NS(price=100.0, currency="ILS", market="TASE"))
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "sleeve_probe@example.com",
                               [_pos("MSFT", 100, 100.0), _pos("V", 20, 100.0)])
            await set_cash(s, user, 5000.0)
            before = {p.ticker: float(p.quantity) for p in await list_positions(s, user)}
            res = await ss.load_basket(s, user, TREND, sleeve_pct=20)
            after = {p.ticker: float(p.quantity) for p in await list_positions(s, user)}
            cash = await get_cash(s, user)
    finally:
        await eng.dispose()

    assert res["ok"] and res["mode"] == ss.FUND and res["dry_run"] is False
    assert res["bought"] and res["bought"][0]["ticker"] == "TQQQ"
    # The whole point: nothing else moved.
    assert after["MSFT"] == before["MSFT"]
    assert after["V"] == before["V"]
    assert "TQQQ" in after
    assert cash < 5000.0            # the sleeve was paid for out of spendable cash
    assert res["broker_note"]


@pytest.mark.asyncio
async def test_a_dry_run_names_the_legs_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(ss, "guarded_quote",
                        lambda tk: NS(price=100.0, currency="ILS", market="TASE"))
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "dryrun_probe@example.com", [_pos("MSFT", 100, 100.0)])
            await set_cash(s, user, 5000.0)
            res = await ss.load_basket(s, user, TREND, sleeve_pct=20, dry_run=True)
            tickers = {p.ticker for p in await list_positions(s, user)}
            cash = await get_cash(s, user)
    finally:
        await eng.dispose()

    assert res["ok"] and res["dry_run"] is True
    assert res["buys"] and res["funding"] is not None
    assert "TQQQ" not in tickers            # nothing bought
    assert cash == pytest.approx(5000.0)    # nothing spent


@pytest.mark.asyncio
async def test_an_unfundable_sleeve_abstains_with_a_reason(monkeypatch):
    """Partially executing would leave a position nobody chose, at a size nobody chose.

    Regression: the first cut gated on ``shortfall >= MIN_TRADE_ILS``. On this
    exact book — ₪1,000 of MSFT, no cash, a 90% sleeve — the funding engine
    raises ₪700 and stops (the ₪200 remainder is below the minimum worthwhile
    trade). ₪200 slipped under that gate, so a 90% sleeve quietly installed
    itself at 70%. Shekels were the wrong unit: the sleeve is chosen in points of
    NAV, so the shortfall has to be judged in points of NAV.
    """
    monkeypatch.setattr(ss, "guarded_quote",
                        lambda tk: NS(price=100.0, currency="ILS", market="TASE"))
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "abstain_probe@example.com", [_pos("MSFT", 10, 100.0)])
            res = await ss.load_basket(s, user, TREND, sleeve_pct=90)
            rows = {p.ticker: float(p.quantity) for p in await list_positions(s, user)}
    finally:
        await eng.dispose()

    assert res["ok"] is False
    assert res["funding"] is not None            # says how far it got, not just "no"
    # The refusal is actionable: it names the sleeve that WOULD work.
    assert res["chosen_sleeve_pct"] == pytest.approx(90.0, abs=0.5)
    assert res["achievable_sleeve_pct"] < res["chosen_sleeve_pct"]
    assert f"{res['achievable_sleeve_pct']:.0f}%" in res["reason"]
    # Nothing was half-executed: no sleeve bought, no funding leg sold.
    assert "TQQQ" not in rows
    assert rows["MSFT"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_a_shortfall_under_one_point_of_nav_still_funds(monkeypatch):
    """The tolerance has to let rounding through, or nothing ever funds.

    A big book with plenty of spendable cash funds the sleeve exactly; the guard
    must not fire on the sub-point residue left by whole-share sizing.
    """
    monkeypatch.setattr(ss, "guarded_quote",
                        lambda tk: NS(price=100.0, currency="ILS", market="TASE"))
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "tolerance_probe@example.com", [_pos("MSFT", 100, 100.0)])
            await set_cash(s, user, 8000.0)
            res = await ss.load_basket(s, user, TREND, sleeve_pct=20, dry_run=True)
    finally:
        await eng.dispose()

    assert res["ok"] is True
    assert res["chosen_sleeve_pct"] - res["achievable_sleeve_pct"] < ss.SLEEVE_SHORTFALL_TOLERANCE_PCT


@pytest.mark.asyncio
async def test_replace_still_replaces_but_first_names_what_it_destroys(monkeypatch):
    monkeypatch.setattr(ss, "guarded_quote",
                        lambda tk: NS(price=100.0, currency="ILS", market="TASE"))
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "replace_probe@example.com",
                               [_pos("MSFT", 100, 100.0), _pos("V", 20, 100.0)])
            preview = await ss.load_basket(s, user, TREND, sleeve_pct=20,
                                           mode="replace", dry_run=True)
            still_there = {p.ticker for p in await list_positions(s, user)}
            done = await ss.load_basket(s, user, TREND, sleeve_pct=20, mode="replace")
            after = {p.ticker for p in await list_positions(s, user)}
    finally:
        await eng.dispose()

    named = {x["ticker"] for x in preview["removing"]}
    assert {"MSFT", "V"} <= named
    assert preview["removing_value_ils"] > 0
    assert {"MSFT", "V"} <= still_there          # the preview destroyed nothing
    assert done["ok"] and done["mode"] == ss.REPLACE
    assert "MSFT" not in after and "V" not in after     # replace still replaces
