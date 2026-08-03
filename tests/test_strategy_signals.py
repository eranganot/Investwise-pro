"""Phase D: the active rule speaks only when it changes its mind.

A trend or swing rule emits a target every session and almost always repeats
yesterday's. Notifying on the target rather than on the change would produce a
daily message that says nothing, which is how people learn to ignore an app.
"""
import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import datetime as dt

import numpy as np
import pytest

from app.services import strategy_signal_service as sig


def _dates(n: int, end: dt.date | None = None) -> list[str]:
    end = end or dt.date.today()
    return [(end - dt.timedelta(days=int((n - 1 - i) * 1.4))).isoformat() for i in range(n)]


def _series(values, end=None):
    return list(zip(_dates(len(values), end), [float(v) for v in values]))


N = 500
_RISING = 100 * np.cumprod(1 + np.full(N, 0.0012))
_FALLING = np.concatenate([100 * np.cumprod(1 + np.full(300, 0.0012)),
                           100 * np.cumprod(1 + np.full(300, 0.0012))[-1]
                           * np.cumprod(1 + np.full(N - 300, -0.004))])
_CASH = 100 * np.cumprod(1 + np.full(N, 0.03 / 252))


def _feed(qqq, tqqq=None):
    tqqq = tqqq if tqqq is not None else qqq
    return {"QQQ": _series(qqq), "TQQQ": _series(tqqq), "BIL": _series(_CASH)}


def test_an_uptrend_puts_the_trend_strategy_in_its_aggressive_sleeve():
    r = sig.evaluate("btm_trend_tqqq", _feed(_RISING))
    assert r["ok"], r
    assert "TQQQ" in r["target"]


def test_a_downtrend_moves_it_back_to_the_core_not_to_cash():
    r = sig.evaluate("btm_trend_tqqq", _feed(_FALLING))
    assert r["ok"], r
    assert "TQQQ" not in r["target"]
    assert "QQQ" in r["target"]
    assert "core holding" in r["describes"]


def test_a_stale_feed_refuses_to_produce_a_signal():
    """'The rule says move to cash', derived from week-old closes, reads as
    today's instruction while describing last week's market."""
    old_end = dt.date.today() - dt.timedelta(days=30)
    feed = {"QQQ": _series(_RISING, old_end), "TQQQ": _series(_RISING, old_end),
            "BIL": _series(_CASH, old_end)}
    r = sig.evaluate("btm_trend_tqqq", feed)
    assert r["ok"] is False and r["reason"] == "STALE_FEED"
    assert "days old" in r["detail"]


def test_an_unknown_strategy_abstains():
    assert sig.evaluate("not_a_strategy", _feed(_RISING))["reason"] == "UNKNOWN_STRATEGY"


def test_a_missing_ticker_abstains_rather_than_guessing():
    r = sig.evaluate("btm_trend_tqqq", {"QQQ": _series(_RISING)})
    assert r["ok"] is False and r["reason"] == "MISSING_TICKER"


def test_too_little_history_abstains():
    short = {k: v[-100:] for k, v in _feed(_RISING).items()}
    r = sig.evaluate("btm_trend_tqqq", short)
    assert r["ok"] is False and r["reason"] == "INSUFFICIENT_HISTORY"


@pytest.mark.asyncio
async def test_the_first_evaluation_is_a_baseline_not_a_flip():
    """Announcing the first reading as a change would tell the user their
    strategy 'changed' the moment they applied it."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.core.config import get_settings
    from app.models.base import Base
    from app.models.tables import StrategySignalState
    import app.models  # noqa: F401

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with Session() as session:
            session.add(StrategySignalState(subject="a@b.c", strategy_id="btm_trend_tqqq",
                                            target={"TQQQ": 1.0}, as_of="2026-08-03"))
            await session.commit()
            row = await sig._state(session, "a@b.c", "btm_trend_tqqq")
            assert row.flipped_at is None          # a baseline is not news
            assert row.previous_target == {}
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_a_pending_flip_becomes_one_card_that_says_no_order_is_placed():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from datetime import datetime, timezone
    from app.core.config import get_settings
    from app.models.base import Base
    from app.models.tables import Plan, StrategySignalState, User
    import app.models  # noqa: F401

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with Session() as session:
            user = User(email="a@b.c", name="A", role="SuperAdmin")
            session.add(user)
            await session.flush()
            session.add(Plan(user_id=user.id, objective="Grow", risk_tolerance="High",
                             strategy="btm_trend_tqqq"))
            session.add(StrategySignalState(
                subject="a@b.c", strategy_id="btm_trend_tqqq",
                target={"QQQ": 1.0}, previous_target={"TQQQ": 1.0},
                as_of="2026-08-03", flipped_at=datetime.now(timezone.utc)))
            await session.commit()

            cards = await sig.pending_signal_recs(session, user)
            assert len(cards) == 1
            card = cards[0]
            assert card["id"].startswith("stratsig_")
            assert "core holding" in card["action"]
            # The execution firewall, stated on the card itself.
            assert any("No brokerage order is placed" in h for h in card["how"])
            assert any("2026-08-03" in h for h in card["how"])

            # Cleared once acted on -- otherwise it reappears every morning.
            assert await sig.resolve_signal(session, user, "btm_trend_tqqq") is True
            assert await sig.pending_signal_recs(session, user) == []
            assert await sig.resolve_signal(session, user, "btm_trend_tqqq") is False
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_no_card_when_the_applied_strategy_is_not_rule_based():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.core.config import get_settings
    from app.models.base import Base
    from app.models.tables import Plan, User
    import app.models  # noqa: F401

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with Session() as session:
            user = User(email="c@d.e", name="C", role="SuperAdmin")
            session.add(user)
            await session.flush()
            session.add(Plan(user_id=user.id, objective="Grow", risk_tolerance="High",
                             strategy="grow_quality"))     # a static basket
            await session.commit()
            assert await sig.active_strategy_id(session, user) is None
            assert await sig.pending_signal_recs(session, user) == []
    finally:
        await eng.dispose()
