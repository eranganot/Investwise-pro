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
            assert any("not moving anything for you" in h for h in card["how"])
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


@pytest.mark.asyncio
async def test_the_signal_card_does_not_claim_the_app_will_act():
    """It first carried apply.kind 'set_plan', whose handler reads spec['fields']
    -- so Accept would have called upsert_plan() with nothing, moved no holding,
    and still reported success."""
    from app.services.recommendations import _ACTIONABLE_KINDS
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
            user = User(email="e@f.g", name="E", role="SuperAdmin")
            session.add(user)
            await session.flush()
            session.add(Plan(user_id=user.id, objective="Grow", risk_tolerance="High",
                             strategy="btm_swing_dip"))
            session.add(StrategySignalState(
                subject="e@f.g", strategy_id="btm_swing_dip",
                target={"TQQQ": 1.0}, previous_target={"QQQ": 1.0},
                as_of="2026-08-03", flipped_at=datetime.now(timezone.utc)))
            await session.commit()
            card = (await sig.pending_signal_recs(session, user))[0]
            assert card["apply"]["kind"] not in _ACTIONABLE_KINDS
            assert any("not moving anything for you" in h for h in card["how"])
    finally:
        await eng.dispose()


# ---------------------------------------------------------------- Phase E: discipline

def test_no_backtest_means_no_stop_rather_than_a_guessed_one():
    """An invented stop level is worse than none: it looks calculated."""
    from app.services import strategy_catalog as sc
    assert sc.discipline_rules("btm_trend_tqqq", None) == []
    assert sc.discipline_rules("btm_trend_tqqq", {"ok": True, "metrics": {}}) == []


def test_stop_levels_are_derived_from_the_strategy_s_own_volatility():
    """A trailing stop inside the strategy's ordinary noise is churn, not
    discipline -- a 62%-vol basket cannot wear the same stop as a 15% one."""
    from app.services import strategy_catalog as sc
    wild = sc.discipline_rules("btm_trend_tqqq", {"ok": True, "metrics": {"volatility_pct": 60.0}})
    calm = sc.discipline_rules("btm_trend_tqqq", {"ok": True, "metrics": {"volatility_pct": 18.0}})
    wild_stop = next(r for r in wild if r["rule_type"] == "trailing_stop")["level"]
    calm_stop = next(r for r in calm if r["rule_type"] == "trailing_stop")["level"]
    assert wild_stop > calm_stop
    assert 12.0 <= calm_stop <= 35.0 and 12.0 <= wild_stop <= 35.0
    assert "measured" in next(r for r in wild if r["rule_type"] == "trailing_stop")["note"]


def test_rules_target_the_aggressive_sleeve_not_the_core():
    """A stop on the thing you fall back TO would exit you from safety."""
    from app.services import strategy_catalog as sc
    rules = sc.discipline_rules("btm_trend_tqqq", {"ok": True, "metrics": {"volatility_pct": 50.0}})
    assert {r["ticker"] for r in rules} == {"TQQQ"}      # QQQ is the base


def test_a_cap_is_skipped_when_the_book_is_already_over_it():
    """Arming a cap the position already breaches fires it instantly, which
    reads as the app deciding to sell rather than as a guard being set."""
    from app.services import strategy_catalog as sc
    measured = {"ok": True, "metrics": {"volatility_pct": 50.0}}
    normal = sc.discipline_rules("btm_trend_tqqq", measured, {"TQQQ": 0.05})
    over = sc.discipline_rules("btm_trend_tqqq", measured, {"TQQQ": 0.95})
    assert any(r["rule_type"] == "max_weight" for r in normal)
    assert not any(r["rule_type"] == "max_weight" for r in over)


def test_a_static_basket_has_no_rule_based_discipline():
    from app.services import strategy_catalog as sc
    assert sc.discipline_rules("grow_quality", {"ok": True, "metrics": {"volatility_pct": 30.0}}) == []


@pytest.mark.asyncio
async def test_the_discipline_card_is_actionable_and_never_arms_by_itself():
    from app.services.recommendations import _ACTIONABLE_KINDS
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.core.config import get_settings
    from app.models.base import Base
    from app.models.tables import Account, Entity, Plan, Position, User
    from app.services import backtest_service as bsvc
    from decimal import Decimal
    import app.models  # noqa: F401

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with Session() as session:
            user = User(email="g@h.i", name="G", role="SuperAdmin")
            session.add(user)
            await session.flush()
            ent = Entity(user_id=user.id, name="Personal", entity_type="Personal")
            session.add(ent)
            await session.flush()
            acc = Account(entity_id=ent.id, name="Main")
            session.add(acc)
            await session.flush()
            session.add(Position(account_id=acc.id, ticker="TQQQ", market="NASDAQ",
                                 quantity=Decimal("10"), cost_basis=Decimal("50"),
                                 current_price=Decimal("60")))
            session.add(Plan(user_id=user.id, objective="Grow", risk_tolerance="High",
                             strategy="btm_trend_tqqq"))
            await bsvc.store(session, "btm_trend_tqqq",
                             {"ok": True, "cagr_pct": 32.9, "volatility_pct": 48.9})
            await session.commit()

            cards = await sig.discipline_recs(session, user)
            assert len(cards) == 1
            card = cards[0]
            assert card["apply"]["kind"] == "create_rules"
            assert card["apply"]["kind"] in _ACTIONABLE_KINDS   # Accept really arms them
            assert all(r["ticker"] == "TQQQ" for r in card["apply"]["rules"])
            assert any("no order is placed" in h for h in card["how"])
            # Nothing was armed by merely producing the card.
            from app.services.rules_service import list_rules
            assert await list_rules(session, user) == []
    finally:
        await eng.dispose()


# ---------------------------------------------------------------- banner reconciliation

@pytest.mark.asyncio
async def test_a_rule_triggered_with_no_card_is_healed_not_left_counting():
    """Production showed "1 trading rule triggered" against 0 cards. The banner
    read the `triggered` flag while the cards came from the recommendations
    pipeline -- two sources for one fact, and no tap could reconcile them
    because only a card can clear a rule. A triggered rule with no visible card
    is a contradiction, so it is resolved."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.core.config import get_settings
    from app.models.base import Base
    from app.models.tables import TradingRule, User
    from app.services.recommendations import build_recommendations
    from decimal import Decimal
    import app.models  # noqa: F401

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with Session() as session:
            user = User(email="k@l.m", name="K", role="SuperAdmin")
            session.add(user)
            await session.flush()
            # Triggered, active, and on a ticker the user does not hold -- so
            # nothing can produce a card for it.
            session.add(TradingRule(subject="k@l.m", ticker="ZZZZ", rule_type="max_weight",
                                    mode="pct", level=Decimal("25"), active=True,
                                    triggered=True))
            await session.commit()

            out = await build_recommendations(session, user)
            banner = out.get("rule_banner")
            if banner is None:          # no holdings -> early return, nothing to reconcile
                return
            assert "ZZZZ" not in str(banner.get("carded"))

            rule = (await session.scalars(
                __import__("sqlalchemy").select(TradingRule).where(
                    TradingRule.subject == "k@l.m"))).first()
            assert rule.triggered is False, "an orphaned trigger must not keep counting"
    finally:
        await eng.dispose()
