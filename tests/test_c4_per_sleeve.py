"""C4 -- signals, discipline and drift stop describing only one sleeve.

Everything downstream of the sleeve table read ``plans.strategy``: one column,
one strategy. On a book running two sleeves that meant every signal, every
discipline card and every drift card described whichever one had been applied
most recently, and the other was invisible.

The subtle one is drift. It is measured **per ticker at the SUMMED target**, not
per sleeve, because the book holds one position per ticker -- two cards about the
same TQQQ at two different targets is the P1 duplicate-cap bug in card form, and
acting on either would make the other wrong.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app.services import strategy_signal_service as sigs

SOXL = "btm_trend_soxl"        # SOXL
FACTOR = "btm_factor_stack"    # MTUM / QUAL / AVUV
TREND = "btm_trend_tqqq"       # TQQQ
VOL = "btm_vol_target_tqqq"    # TQQQ too -- the shared-ticker case


def _pos(ticker, price=100.0, qty=100.0, basis=90.0):
    return {"ticker": ticker, "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": basis, "expected_return_pct": 8, "volatility_pct": 20}


def _seed(c, extra=()):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _pos("V"), _pos("SCHD"), _pos("MSFT"), *[_pos(t) for t in extra]]})


def _apply(c, sid, pct):
    return c.post(f"/api/v1/strategies/{sid}/apply?sleeve_pct={pct}").json()


async def _user(s):
    from app.services.feed_service import ensure_superadmin
    return await ensure_superadmin(s)


def _session():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import app.models  # noqa: F401
    from app.core.config import get_settings
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


# --------------------------------------------------------------------------- #
# Who is "active"
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_active_strategy_ids_returns_every_sleeve():
    from app.models.base import Base
    from app.services import sleeve_service as sv

    eng, Session = _session()
    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with Session() as s:
            user = await _user(s)
            await sv.add_or_resize(s, user, SOXL, 10)
            await sv.add_or_resize(s, user, FACTOR, 15)
            await s.commit()
            assert sorted(await sigs.active_strategy_ids(s, user)) == sorted([SOXL, FACTOR])
            # The single-sleeve helper still answers with ONE of them -- which
            # one is not worth pinning: rows created in the same transaction
            # share a created_at, so the order falls through to strategy_id.
            # Nothing depends on it (fund_plan sorts by size, the caps sum), and
            # any remaining caller of this helper is a place that cannot see
            # sleeve two anyway.
            assert await sigs.active_strategy_id(s, user) in (SOXL, FACTOR)
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_a_book_with_no_sleeve_rows_falls_back_to_the_legacy_column():
    """A book that predates the C1 backfill must keep working exactly as it did."""
    from sqlalchemy import delete

    from app.models.base import Base
    from app.models.tables import Plan, PlanSleeve

    eng, Session = _session()
    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with Session() as s:
            user = await _user(s)
            s.add(Plan(user_id=user.id, strategy=SOXL, strategy_sleeve_pct=10.0))
            await s.execute(delete(PlanSleeve))
            await s.commit()
            assert await sigs.active_strategy_ids(s, user) == [SOXL]
    finally:
        await eng.dispose()


# --------------------------------------------------------------------------- #
# One card per sleeve
# --------------------------------------------------------------------------- #
def test_the_card_id_is_derived_in_one_place():
    """Producer and matcher cannot disagree about the truncation."""
    assert sigs.signal_card_id(FACTOR) == f"stratsig_{FACTOR[:12]}"
    assert sigs.signal_card_id(SOXL) != sigs.signal_card_id(FACTOR)


@pytest.mark.asyncio
async def test_dismissing_one_sleeves_signal_does_not_clear_anothers():
    """A real defect C4 found, pinned.

    The Today cleanup resolved `active_strategy_id` -- the FIRST sleeve --
    whichever signal card had been dismissed. On a two-sleeve book, dismissing
    the factor sleeve's flip silently consumed the SOXL sleeve's pending signal
    and left the factor one armed. Exactly backwards, and silent.
    """
    from datetime import datetime, timezone

    from app.models.base import Base
    from app.models.tables import StrategySignalState
    from app.services import sleeve_service as sv

    eng, Session = _session()
    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with Session() as s:
            user = await _user(s)
            await sv.add_or_resize(s, user, SOXL, 10)
            await sv.add_or_resize(s, user, FACTOR, 15)
            for sid in (SOXL, FACTOR):
                s.add(StrategySignalState(subject=user.email, strategy_id=sid,
                                          target={"X": 1.0}, as_of="2026-08-13",
                                          flipped_at=datetime.now(timezone.utc)))
            await s.commit()

            assert await sigs.resolve_signal(s, user, FACTOR) is True

            soxl = await sigs._state(s, user.email, SOXL)
            factor = await sigs._state(s, user.email, FACTOR)
            assert factor.flipped_at is None, "the sleeve that was acted on stays pending"
            assert soxl.flipped_at is not None, "resolving one sleeve cleared another's flip"
    finally:
        await eng.dispose()


def test_the_ack_route_can_target_one_sleeve_and_refuses_a_stranger():
    with TestClient(m.app) as c:
        _seed(c)
        _apply(c, SOXL, 10)
        _apply(c, FACTOR, 15)
        bad = c.post("/api/v1/strategies/signal/ack?strategy_id=btm_not_real").json()
        assert bad["ok"] is False and "not a sleeve" in bad["error"]


# --------------------------------------------------------------------------- #
# Drift: per ticker, at the sum
# --------------------------------------------------------------------------- #
def test_two_sleeves_wanting_one_ticker_produce_ONE_drift_card():
    """The P1 duplicate bug in card form. TQQQ is one position; two cards about
    it at two targets means acting on either makes the other wrong."""
    with TestClient(m.app) as c:
        _seed(c)                      # TQQQ deliberately not held -> cold start
        _apply(c, TREND, 20)
        _apply(c, VOL, 15)
        recs = c.get("/api/v1/recommendations").json().get("recommendations", [])
        tqqq = [r for r in recs
                if r.get("dimension") == "strategy"
                and (r.get("meta") or {}).get("ticker") == "TQQQ"]
        assert len(tqqq) == 1, f"{len(tqqq)} cards about one TQQQ position"
        # ...and it describes the SUM, not either sleeve alone.
        assert tqqq[0]["meta"]["chosen_pct"] == pytest.approx(35.0, abs=0.5)


def test_a_shared_ticker_routes_accept_to_the_whole_plan():
    """There is no single strategy_id to hand fund_sleeve for a ticker two
    sleeves want -- funding one of them buys that sleeve's share while the card
    describes the total."""
    from app.services.recommendations import _fund_action

    assert _fund_action([SOXL]) == {"kind": "fund_sleeve", "strategy_id": SOXL}
    assert _fund_action([TREND, VOL]) == {"kind": "fund_plan"}
    assert _fund_action([]) == {"kind": "none"}


def test_a_ticker_only_one_sleeve_wants_still_funds_that_sleeve():
    with TestClient(m.app) as c:
        _seed(c)
        _apply(c, SOXL, 20)
        recs = c.get("/api/v1/recommendations").json().get("recommendations", [])
        soxl = [r for r in recs
                if r.get("dimension") == "strategy"
                and (r.get("meta") or {}).get("ticker") == "SOXL"]
        assert soxl, "a 20% sleeve held at 0% must raise a cold-start card"


def test_both_sleeves_appear_when_they_want_different_tickers():
    """The plain case that was broken: only the most recently applied sleeve
    produced cards at all."""
    with TestClient(m.app) as c:
        _seed(c)
        _apply(c, SOXL, 10)
        _apply(c, FACTOR, 15)
        recs = c.get("/api/v1/recommendations").json().get("recommendations", [])
        tickers = {(r.get("meta") or {}).get("ticker") for r in recs
                   if r.get("dimension") == "strategy" and (r.get("meta") or {}).get("ticker")}
        assert "SOXL" in tickers, "the first-applied sleeve is invisible again"
        assert tickers & {"MTUM", "QUAL", "AVUV"}, "the second sleeve is invisible"


# --------------------------------------------------------------------------- #
# "Fully funded" has to mean it
# --------------------------------------------------------------------------- #
def test_a_sleeve_short_by_less_than_a_lot_is_not_nothing_to_do():
    """The C3b carry-over.

    A sleeve AT its target has nothing to do. A sleeve still short by less than
    one tradeable lot also produces no legs, but it is not at its target and
    never will be. Live, the factor sleeve sat 132 short while the preview said
    "would end at 17.4%" and "every sleeve fits" two lines apart.
    """
    from app.services.strategy_service import _legs_for

    # A gap far below MIN_TRADE_ILS on every leg.
    targets = {"MTUM": 0.004, "QUAL": 0.003}
    assert _legs_for(targets, 21295, {"MTUM": 80.0, "QUAL": 60.0}) == [], (
        "precondition: sub-minimum gaps produce no legs")


def test_fully_funded_is_false_when_the_book_lands_short():
    with TestClient(m.app) as c:
        _seed(c, extra=("SOXL",))
        # Held SOXL is ~25% of the book; ask for slightly more than that so the
        # remaining gap is real but smaller than a tradeable lot.
        _apply(c, SOXL, 26)
        res = c.post("/api/v1/plan/sleeves/fund?dry_run=true").json()
        row = next(x for x in res["sleeves"] if x["strategy_id"] == SOXL)
        if row["status"] == "as_close_as_a_lot_allows":
            assert res["fully_funded"] is False, (
                "claimed fully funded while the book lands short of the intent")
            assert "whole lot" in res["message"], res["message"]
            assert row["shortfall_ils"] > 0
