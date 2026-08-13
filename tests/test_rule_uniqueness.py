"""A ticker carries at most one active protective rule of each kind.

Found on the live Today screen, not by reading code. Three cards were proposing
rules on the same tickers at once:

    "Arm the discipline for Factor Stack"  -> trailing stop MTUM 12%
    "6 protective rules ready to arm"      -> 6 rules across AVUV, MTUM, QUAL
    "MTUM is in an uptrend"                -> trailing stop MTUM 15%

Three independent producers, and none can see what the others propose on the
same screen -- each filters only against rules ALREADY armed. `create_rule` had
no uniqueness rule, so accepting two left MTUM carrying a 12% stop and a 15% one,
both active, no warning. The tighter one fires first, so you are stopped out at
12% having decided on 15%.

The codebase had already reached this conclusion for `max_weight` and never
generalised it. These tests are the generalisation.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest

from app.services.rules_service import (
    _ONE_PER_TICKER, conflicting_rule_level, create_rule)


def _session():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import app.models  # noqa: F401
    from app.core.config import get_settings
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


async def _fresh():
    from app.models.base import Base
    from app.services.feed_service import ensure_superadmin
    eng, Session = _session()
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    s = Session()
    return eng, s, await ensure_superadmin(s)


async def _active(s, user, ticker, rule_type):
    """Straight from the table, deliberately.

    `list_rules` retires a rule whose ticker is not in the book, at read time
    (P4). These tests exercise the WRITE path and seed no positions, so going
    through the serializer would measure the retirement sweep instead. The C2 cap
    tests fell into exactly this and it cost a debugging round.
    """
    from sqlalchemy import select

    from app.models.tables import TradingRule
    rows = (await s.scalars(select(TradingRule).where(
        TradingRule.subject == user.email,
        TradingRule.ticker == ticker.upper(),
        TradingRule.rule_type == rule_type,
        TradingRule.active.is_(True)))).all()
    return [{"ticker": r.ticker, "rule_type": r.rule_type, "level": float(r.level),
             "active": r.active, "strategy_id": r.strategy_id} for r in rows]


@pytest.mark.asyncio
async def test_the_exact_collision_from_the_live_screen():
    """Discipline card 12%, then momentum card 15%, on MTUM."""
    eng, s, user = await _fresh()
    try:
        await create_rule(s, user, ticker="MTUM", rule_type="trailing_stop",
                          mode="pct", level=12.0, note="Factor Stack discipline")
        await create_rule(s, user, ticker="MTUM", rule_type="trailing_stop",
                          mode="pct", level=15.0, note="MTUM is in an uptrend")
        rules = await _active(s, user, "MTUM", "trailing_stop")
        assert len(rules) == 1, f"{len(rules)} trailing stops on MTUM"
        assert rules[0]["level"] == pytest.approx(15.0), "the later decision wins"
    finally:
        await s.close()
        await eng.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("rule_type", sorted(_ONE_PER_TICKER))
async def test_every_protective_kind_is_one_per_ticker(rule_type):
    eng, s, user = await _fresh()
    try:
        await create_rule(s, user, ticker="V", rule_type=rule_type, mode="pct", level=10.0)
        await create_rule(s, user, ticker="V", rule_type=rule_type, mode="pct", level=20.0)
        rules = await _active(s, user, "V", rule_type)
        assert len(rules) == 1, f"{rule_type} stacked: {len(rules)} rules on V"
        assert rules[0]["level"] == pytest.approx(20.0)
    finally:
        await s.close()
        await eng.dispose()


@pytest.mark.asyncio
async def test_a_re_levelled_rule_re_arms():
    """A rule that had already fired must not stay latched at its new level --
    it would be armed and triggered at the same time."""
    from sqlalchemy import select

    from app.models.tables import TradingRule
    eng, s, user = await _fresh()
    try:
        r = await create_rule(s, user, ticker="V", rule_type="trailing_stop",
                              mode="pct", level=10.0)
        r.triggered = True
        await s.commit()
        await create_rule(s, user, ticker="V", rule_type="trailing_stop",
                          mode="pct", level=18.0)
        row = (await s.scalars(select(TradingRule).where(
            TradingRule.ticker == "V", TradingRule.rule_type == "trailing_stop"))).one()
        assert row.level == pytest.approx(18.0)
        assert row.triggered is False, "re-levelled at a new level but still latched"
    finally:
        await s.close()
        await eng.dispose()


# --------------------------------------------------------------------------- #
# What must STILL be allowed to coexist
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_two_price_alerts_on_one_ticker_are_legitimate():
    """"Tell me at 100 and again at 120" is a real thing to want, and two
    alerts do not contradict each other the way two stops do."""
    eng, s, user = await _fresh()
    try:
        await create_rule(s, user, ticker="V", rule_type="price_above",
                          mode="price", level=100.0)
        await create_rule(s, user, ticker="V", rule_type="price_above",
                          mode="price", level=120.0)
        assert len(await _active(s, user, "V", "price_above")) == 2
    finally:
        await s.close()
        await eng.dispose()


@pytest.mark.asyncio
async def test_entry_and_exit_signals_on_one_ticker_coexist():
    eng, s, user = await _fresh()
    try:
        for mode in ("entry", "exit"):
            await create_rule(s, user, ticker="TQQQ", rule_type="strategy_signal",
                              mode=mode, level=0.0, strategy_id="btm_trend_tqqq")
        assert len(await _active(s, user, "TQQQ", "strategy_signal")) == 2
    finally:
        await s.close()
        await eng.dispose()


@pytest.mark.asyncio
async def test_two_sleeves_may_both_subscribe_to_one_tickers_signal():
    """TQQQ is wanted by btm_trend_tqqq and btm_vol_target_tqqq. Each sleeve's
    subscription is its own rule -- collapsing them would silently unsubscribe
    one sleeve."""
    eng, s, user = await _fresh()
    try:
        for sid in ("btm_trend_tqqq", "btm_vol_target_tqqq"):
            await create_rule(s, user, ticker="TQQQ", rule_type="strategy_signal",
                              mode="entry", level=0.0, strategy_id=sid)
        rules = await _active(s, user, "TQQQ", "strategy_signal")
        assert len(rules) == 2
        assert {r["strategy_id"] for r in rules} == {"btm_trend_tqqq", "btm_vol_target_tqqq"}
    finally:
        await s.close()
        await eng.dispose()


@pytest.mark.asyncio
async def test_a_retired_rule_does_not_block_arming_a_fresh_one():
    """Only ACTIVE rules conflict. A retired stop is history, not a claim on
    the ticker."""
    from sqlalchemy import select

    from app.models.tables import TradingRule
    eng, s, user = await _fresh()
    try:
        r = await create_rule(s, user, ticker="V", rule_type="stop_loss",
                              mode="pct", level=10.0)
        r.active = False
        await s.commit()
        await create_rule(s, user, ticker="V", rule_type="stop_loss", mode="pct", level=25.0)
        rows = (await s.scalars(select(TradingRule).where(
            TradingRule.ticker == "V", TradingRule.rule_type == "stop_loss"))).all()
        assert len(rows) == 2, "history should be kept, not overwritten"
        assert len(await _active(s, user, "V", "stop_loss")) == 1
    finally:
        await s.close()
        await eng.dispose()


# --------------------------------------------------------------------------- #
# The confirmation has to say what happened
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_accept_confirmation_says_relevelled_not_armed():
    """Reporting "armed" when a 12% stop just became a 15% one is claiming
    something that did not happen."""
    from app.services.recommendations import _create_rule_from_spec
    eng, s, user = await _fresh()
    try:
        first = await _create_rule_from_spec(s, user, {
            "ticker": "MTUM", "rule_type": "trailing_stop", "mode": "pct", "level": 12})
        assert first["action"] == "armed" and first["previous_level"] is None

        second = await _create_rule_from_spec(s, user, {
            "ticker": "MTUM", "rule_type": "trailing_stop", "mode": "pct", "level": 15})
        assert second["action"] == "relevelled"
        assert second["previous_level"] == pytest.approx(12.0)
        assert "was 12%" in second["label"], second["label"]
    finally:
        await s.close()
        await eng.dispose()


@pytest.mark.asyncio
async def test_the_level_is_read_before_the_write():
    """conflicting_rule_level must report what WOULD be replaced. Reading it
    after create_rule would always return the new value."""
    eng, s, user = await _fresh()
    try:
        assert await conflicting_rule_level(s, user, "V", "trailing_stop", "pct") is None
        await create_rule(s, user, ticker="V", rule_type="trailing_stop",
                          mode="pct", level=9.0)
        assert await conflicting_rule_level(s, user, "v", "trailing_stop", "pct") == 9.0
        # A kind that is allowed to stack reports nothing to replace.
        assert await conflicting_rule_level(s, user, "V", "price_above", "price") is None
    finally:
        await s.close()
        await eng.dispose()
