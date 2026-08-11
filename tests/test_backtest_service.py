"""Phase B: backtests are precomputed and served from storage, never on request.

Ten years of daily closes per ticker is far too much network for a page load,
and it would make the strategy list fail whenever a price provider is down. The
route reads rows; a scheduled job writes them.
"""
import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app.services import backtest_service as svc
from app.services import strategy_catalog


def test_the_catalog_specs_are_all_shaped_for_the_engine():
    from app.engines import strategy_backtest as bt
    for spec in strategy_catalog.backtestable():
        assert spec["id"]
        assert spec.get("weights"), f"{spec['id']} has no basket"
        kind = (spec.get("overlay") or {}).get("kind", "buy_hold")
        assert kind in {"buy_hold", "trend_filter", "ma_cross", "donchian",
                        "rsi_pullback", "dual_momentum", "vol_target",
                        "drawdown_brake", "sector_momentum"}
        assert bt.tickers_needed(spec), f"{spec['id']} references no tickers"


def test_no_shipped_strategy_is_built_on_a_measured_failure():
    """drawdown_brake capped nothing when measured and failed out of sample."""
    from app.engines.strategy_backtest import MEASURED_FAILURES
    for spec in strategy_catalog.backtestable():
        kind = (spec.get("overlay") or {}).get("kind", "buy_hold")
        assert kind not in MEASURED_FAILURES, f"{spec['id']} uses a known-broken overlay"


def test_every_swing_strategy_falls_back_to_a_core_not_to_cash():
    """The correction that took the dip-buy from 4.15%/yr to 15.64%/yr."""
    for entry in strategy_catalog.CATALOG:
        kind = (entry.get("overlay") or {}).get("kind", "buy_hold")
        if kind in {"rsi_pullback", "donchian", "trend_filter", "vol_target"}:
            assert entry.get("base"), f"{entry['id']} would sit in cash between setups"


def test_ids_are_unique():
    ids = strategy_catalog.ids()
    assert len(ids) == len(set(ids))


def test_the_existing_four_goals_are_untouched_by_the_fifth():
    """Adding a family must not disturb the ones people already use."""
    from app.services import strategies as legacy
    with TestClient(m.app) as c:
        body = c.get("/api/v1/strategies").json()
        # Beat the Market now LEADS, so the four legacy goals follow it. Their
        # relative order, and everything about their cards, is unchanged.
        assert body["goals"][1:] == legacy.GOAL_ORDER
        for g in legacy.GOAL_ORDER:
            cards = body["by_goal"][g]
            assert cards and all("profile" in s for s in cards)   # still derived
            assert all(not s.get("measured") for s in cards)
        assert body["backtest_engine_version"]


def test_backtests_route_reports_what_has_never_been_computed():
    with TestClient(m.app) as c:
        body = c.get("/api/v1/strategies/backtests").json()
        assert body["never_computed"] == strategy_catalog.ids()
        assert len(body["strategies"]) == len(strategy_catalog.CATALOG)
        # Nothing computed yet, so each card must say so rather than show a zero.
        assert all(s["backtest"] is None for s in body["strategies"])
        # The mechanical spec stays server-side; the card gets prose + tickers.
        assert "overlay" not in body["strategies"][0]


def test_baskets_are_emitted_in_the_shape_the_plan_renderer_already_expects():
    with TestClient(m.app) as c:
        for s in c.get("/api/v1/strategies/backtests").json()["strategies"]:
            for leg in s["basket"]:
                assert len(leg) == 2 and isinstance(leg[0], str)
                assert isinstance(leg[1], (int, float))


@pytest.mark.asyncio
async def test_a_stored_result_is_served_back_with_its_provenance():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.core.config import get_settings
    from app.models.base import Base
    import app.models  # noqa: F401

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with Session() as session:
            await svc.store(session, "btm_trend_tqqq", {
                "ok": True, "cagr_pct": 32.79, "max_drawdown_pct": 62.7,
                "start": "2016-08-03", "end": "2026-08-03", "observations": 2513,
                "robustness": {"out_of_sample": {"verdict": "holds up"}},
            })
            await session.commit()
            got = await svc.get_many(session, ["btm_trend_tqqq"])
        row = got["btm_trend_tqqq"]
        assert row["ok"] and row["metrics"]["cagr_pct"] == 32.79
        assert row["period"] == {"start": "2016-08-03", "end": "2026-08-03",
                                 "observations": 2513}
        assert row["engine_version"] == svc.ENGINE_VERSION
        assert row["stale"] is False
        assert row["robustness"]["out_of_sample"]["verdict"] == "holds up"
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_an_abstention_is_visible_without_pretending_the_row_is_healthy():
    """The reason surfaces and ok flips false, but the last good measurement is
    kept: erasing it meant one provider hiccup destroyed all seven at once. The
    row reads "measured, refresh currently failing" rather than either lying
    that it is fine or losing the numbers."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.core.config import get_settings
    from app.models.base import Base
    import app.models  # noqa: F401

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with Session() as session:
            await svc.store(session, "btm_swing_dip", {"ok": True, "cagr_pct": 15.64})
            await session.commit()
            await svc.store(session, "btm_swing_dip",
                            {"ok": False, "reason": "MISSING_TICKER",
                             "detail": "no price history for TQQQ"})
            await session.commit()
            got = (await svc.get_many(session, ["btm_swing_dip"]))["btm_swing_dip"]
        assert got["ok"] is False
        assert got["reason"] == "MISSING_TICKER"
        assert got["refresh_failing"] is True
        assert got["metrics"]["cagr_pct"] == 15.64      # not erased
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_an_old_row_or_a_bumped_engine_version_reads_as_stale():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.core.config import get_settings
    from app.models.base import Base
    from app.models.tables import StrategyBacktest
    import app.models  # noqa: F401

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with Session() as session:
            row = await svc.store(session, "btm_factor_stack", {"ok": True, "cagr_pct": 13.4})
            row.computed_at = datetime.now(timezone.utc) - timedelta(days=svc.STALE_AFTER_DAYS + 1)
            await session.commit()
            assert (await svc.get_many(session, ["btm_factor_stack"]))["btm_factor_stack"]["stale"]

            fresh = await svc.store(session, "btm_factor_stack", {"ok": True, "cagr_pct": 13.4})
            fresh.engine_version = "ancient"
            await session.commit()
            assert (await svc.get_many(session, ["btm_factor_stack"]))["btm_factor_stack"]["stale"]
    finally:
        await eng.dispose()


def test_refresh_never_runs_inside_the_read_routes():
    """A page load must not depend on a price provider being up."""
    import inspect
    for fn in (svc.get_many,):
        assert "measure(" not in inspect.getsource(fn)


# ---------------------------------------------------------------- Phase C: presentation

def test_the_plan_route_now_carries_the_fifth_goal():
    with TestClient(m.app) as c:
        body = c.get("/api/v1/strategies").json()
        assert body["goals"][0] == strategy_catalog.GOAL
        cards = body["by_goal"][strategy_catalog.GOAL]
        assert len(cards) == len(strategy_catalog.CATALOG)


def test_cards_match_the_shape_the_existing_renderer_reads():
    """The Plan tab reads basket as [ticker, weight] pairs and risk_tolerance as
    the Low/Medium/High vocabulary. A card that does not match renders garbage
    rather than failing loudly, so this is asserted rather than assumed."""
    with TestClient(m.app) as c:
        cards = c.get("/api/v1/strategies").json()["by_goal"][strategy_catalog.GOAL]
        for s in cards:
            assert s["risk_tolerance"] in {"Low", "Medium", "High"}
            assert s["name"] and s["description"] and s["rule"]
            for leg in s["basket"]:
                assert len(leg) == 2 and isinstance(leg[0], str)
                assert isinstance(leg[1], (int, float))
            # Measured, not derived: the UI must read `backtest`, not `profile`.
            assert s["measured"] is True
            assert "profile" not in s


def test_an_unmeasured_strategy_still_renders_rather_than_showing_a_blank():
    with TestClient(m.app) as c:
        cards = c.get("/api/v1/strategies").json()["by_goal"][strategy_catalog.GOAL]
        assert all(s["backtest"] is None for s in cards)   # nothing computed in tests
        assert all(s["name"] for s in cards)


def test_the_rule_summary_describes_the_actual_mechanism():
    """The description sells the idea; the rule line states what the code does,
    so a card cannot imply a discipline it does not implement."""
    by_id = {c["id"]: c for c in strategy_catalog.as_plan_cards()}
    assert "200-day" in by_id["btm_trend_tqqq"]["rule"]
    assert "oversold" in by_id["btm_swing_dip"]["rule"]
    assert "20-day high" in by_id["btm_swing_breakout"]["rule"]
    assert "volatility" in by_id["btm_vol_target_tqqq"]["rule"]
    assert "Held throughout" in by_id["btm_factor_stack"]["rule"]


def test_preview_resolves_a_rule_based_id_instead_of_dead_buttons():
    with TestClient(m.app) as c:
        c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
            {"ticker": "TEVA", "market": "TASE", "asset_class": "Equities", "depth": 3,
             "spot_price": 100, "listing_price": 100, "quantity": 100, "cost_basis": 100,
             "expected_return_pct": 7, "volatility_pct": 14}]})
        r = c.get("/api/v1/strategies/btm_trend_tqqq/preview").json()
        assert r["ok"] is True
        assert r["diff"]["risk_tolerance"]["to"] == "High"


def test_an_unknown_id_is_still_rejected():
    with TestClient(m.app) as c:
        assert c.get("/api/v1/strategies/not_a_strategy/preview").json()["ok"] is False


def test_the_applied_target_is_all_equity_not_a_permanent_cash_weight():
    """Time in T-bills is a transient state of the rule, not a target. Writing it
    into target_allocation would make the allocation engine permanently demand
    cash the strategy only wants sometimes."""
    for sid in strategy_catalog.ids():
        legacy = strategy_catalog.as_legacy_strategy(sid)
        assert legacy["target_allocation"] == {"Equities": 1.0}
        assert legacy["objective"] == "Grow"          # DB column is String(16)
        assert len(legacy["objective"]) <= 16


@pytest.mark.asyncio
async def test_a_stale_row_says_whether_it_is_old_or_from_another_engine():
    """The two need different responses: age waits for tonight's job, a version
    mismatch needs a recompute now. Conflating them cost a production run --
    rows from the previous engine reported stale=false and were served as
    current, so every missing field read as 'not measured'."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.core.config import get_settings
    from app.models.base import Base
    import app.models  # noqa: F401

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with Session() as session:
            fresh = await svc.store(session, "btm_trend_soxl", {"ok": True, "cagr_pct": 43.4})
            await session.commit()
            got = (await svc.get_many(session, ["btm_trend_soxl"]))["btm_trend_soxl"]
            assert got["stale"] is False and got["stale_reason"] is None
            assert got["live_engine_version"] == svc.ENGINE_VERSION

            fresh.engine_version = "a1"
            await session.commit()
            got = (await svc.get_many(session, ["btm_trend_soxl"]))["btm_trend_soxl"]
            assert got["stale"] is True and got["stale_reason"] == "engine_version"

            fresh.engine_version = svc.ENGINE_VERSION
            fresh.computed_at = datetime.now(timezone.utc) - timedelta(days=svc.STALE_AFTER_DAYS + 1)
            await session.commit()
            got = (await svc.get_many(session, ["btm_trend_soxl"]))["btm_trend_soxl"]
            assert got["stale"] is True and got["stale_reason"] == "age"
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_a_failed_refresh_does_not_erase_the_last_good_measurement():
    """One provider hiccup abstained all seven strategies at once and left
    nothing behind. "No longer measurable" and "the feed was down for a minute"
    are different failures, and only the first justifies losing the numbers."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.core.config import get_settings
    from app.models.base import Base
    import app.models  # noqa: F401

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with Session() as session:
            await svc.store(session, "btm_trend_tqqq", {
                "ok": True, "cagr_pct": 32.9, "max_drawdown_pct": 62.7,
                "start": "2016-08-03", "end": "2026-08-03", "observations": 2513})
            await session.commit()

            await svc.store(session, "btm_trend_tqqq", {
                "ok": False, "reason": "MISSING_TICKER",
                "detail": "no price history for QQQ, SPY, TQQQ"})
            await session.commit()

            row = (await svc.get_many(session, ["btm_trend_tqqq"]))["btm_trend_tqqq"]
            # The measurement survives...
            assert row["metrics"]["cagr_pct"] == 32.9
            assert row["period"]["observations"] == 2513
            # ...and the failure is visible rather than silent.
            assert row["refresh_failing"] is True
            assert "MISSING_TICKER" in row["last_error"]
            assert row["last_error_at"]

            # A later success clears the failure flag.
            await svc.store(session, "btm_trend_tqqq", {
                "ok": True, "cagr_pct": 33.1, "start": "2016-08-03",
                "end": "2026-08-03", "observations": 2513})
            await session.commit()
            row = (await svc.get_many(session, ["btm_trend_tqqq"]))["btm_trend_tqqq"]
            assert row["refresh_failing"] is False and row["last_error"] is None
            assert row["metrics"]["cagr_pct"] == 33.1
    finally:
        await eng.dispose()
