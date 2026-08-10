"""P4 — entry/exit rules that ARE the strategy, and a drift card that knows the
difference between drifting and never having started.
"""
import inspect

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.tables import StrategySignalState, TradingRule
from app.schemas.intake import IntakePosition
from app.schemas.state_machine import Market
from app.services import recommendations as rr
from app.services import rules_service as rs
from app.services import strategy_catalog as sc
from app.services.feed_service import ensure_user
from app.services.intake_service import (
    ensure_account, ensure_entity, list_positions, upsert_positions)

TREND = "btm_trend_tqqq"
MEASURED = {"ok": True, "metrics": {
    "volatility_pct": 50.0, "max_drawdown_pct": 62.0, "trades_per_year": 6.0,
    "win_rate_pct": 58.0, "avg_holding_days": 41.0,
    "expectancy_pct_per_trade": 1.9, "profit_factor": 1.6}}


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


async def _book(s, email, positions=()):
    user = await ensure_user(s, email)
    entity = await ensure_entity(s, user, "Personal", "Personal")
    account = await ensure_account(s, entity, "Main")
    if positions:
        await upsert_positions(s, account, list(positions))
    await s.commit()
    return user


def _pos(ticker, qty=100, price=100.0):
    return IntakePosition(ticker=ticker, market=Market.TASE, depth=2, spot_price=price,
                          listing_price=price, quantity=qty, cost_basis=price,
                          asset_class="Equities")


# --------------------------------------------------------------------------- #
# P4.1 — the rule IS the strategy
# --------------------------------------------------------------------------- #
def test_entry_exit_rules_do_not_reimplement_the_indicators():
    """The structural claim. Discrete ma_above/rsi_below rule types would mean a
    second copy of SMA/RSI/Donchian living in rules_service beside the backtest
    engine's — the duplication that produced the reprice-loop and regime bugs.
    So rules_service must contain no indicator maths at all."""
    src = inspect.getsource(rs)
    for forbidden in ("def sma", "def rsi", "rolling_max", "np.", "numpy"):
        assert forbidden not in src, f"rules_service grew indicator code: {forbidden}"
    # It reads the recorded flip instead.
    assert "StrategySignalState" in inspect.getsource(rs._signal_flip)


def test_a_signal_rule_carries_measured_statistics_not_invented_ones():
    rules = sc.signal_rules(TREND, MEASURED)
    assert rules, "a measured strategy should offer entry and exit rules"
    for r in rules:
        st = r["stats"]
        assert st["win_rate_pct"] == 58.0
        assert st["avg_holding_days"] == 41.0
        assert st["expectancy_pct_per_trade"] == 1.9
        assert "58%" in r["why"]


def test_no_backtest_means_no_entry_exit_rule_rather_than_a_guessed_one():
    """An armed rule that cannot say how it performed is an opinion wearing a
    rule's clothes."""
    assert sc.signal_rules(TREND, None) == []
    assert sc.signal_rules(TREND, {"ok": True, "metrics": {}}) == []
    # Trades measured but no win rate -> still nothing to quote.
    assert sc.signal_rules(TREND, {"ok": True, "metrics": {"trades_per_year": 6}}) == []


def test_both_sides_are_offered_and_only_for_the_aggressive_leg():
    rules = sc.signal_rules(TREND, MEASURED)
    assert {r["mode"] for r in rules} == {"entry", "exit"}
    assert {r["ticker"] for r in rules} == {"TQQQ"}      # never the QQQ core


@pytest.mark.asyncio
async def test_a_signal_rule_must_pin_the_strategy_it_follows():
    """Unpinned, changing the applied strategy would silently repoint every
    armed rule at a different set of trades."""
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "pin_probe@example.com", [_pos("TQQQ")])
            with pytest.raises(ValueError, match="must name the strategy"):
                await rs.create_rule(s, user, ticker="TQQQ",
                                     rule_type="strategy_signal", mode="entry", level=0)
            with pytest.raises(ValueError, match="mode must be"):
                await rs.create_rule(s, user, ticker="TQQQ",
                                     rule_type="strategy_signal", mode="pct", level=0,
                                     strategy_id=TREND)
            ok = await rs.create_rule(s, user, ticker="TQQQ",
                                      rule_type="strategy_signal", mode="exit",
                                      level=0, strategy_id=TREND)
    finally:
        await eng.dispose()
    assert ok.strategy_id == TREND and ok.mode == "exit"


@pytest.mark.asyncio
async def test_an_exit_rule_fires_on_the_recorded_flip_and_is_executable():
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "flip_probe@example.com", [_pos("TQQQ", 10, 50.0)])
            await rs.create_rule(s, user, ticker="TQQQ", rule_type="strategy_signal",
                                 mode="exit", level=0, strategy_id=TREND)
            # The signal service recorded a flip out of the sleeve.
            s.add(StrategySignalState(subject=user.email, strategy_id=TREND,
                                      target={"QQQ": 1.0}, previous_target={"TQQQ": 1.0},
                                      as_of="2026-08-10",
                                      flipped_at=rs._now()))
            await s.commit()
            newly = await rs.evaluate_user(s, user)
            cards = await rs.triggered_rule_recs(s, user)
    finally:
        await eng.dispose()

    assert len(newly) == 1 and newly[0]["rule_type"] == "strategy_signal"
    assert "wants out" in newly[0]["title"]
    assert cards and cards[0]["apply"]["kind"] == "sell_position"
    assert "no brokerage order" in " ".join(cards[0]["how"]).lower()


@pytest.mark.asyncio
async def test_an_entry_rule_stays_advisory_because_size_is_a_funding_decision():
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "entry_probe@example.com", [_pos("TQQQ", 10, 50.0)])
            await rs.create_rule(s, user, ticker="TQQQ", rule_type="strategy_signal",
                                 mode="entry", level=0, strategy_id=TREND)
            s.add(StrategySignalState(subject=user.email, strategy_id=TREND,
                                      target={"TQQQ": 1.0}, previous_target={"QQQ": 1.0},
                                      as_of="2026-08-10", flipped_at=rs._now()))
            await s.commit()
            newly = await rs.evaluate_user(s, user)
            cards = await rs.triggered_rule_recs(s, user)
    finally:
        await eng.dispose()

    assert len(newly) == 1 and "wants in" in newly[0]["title"]
    assert (cards[0]["apply"] or {}).get("kind") in (None, "none")


@pytest.mark.asyncio
async def test_the_wrong_side_does_not_fire():
    """An exit rule must not fire when the strategy is buying."""
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "side_probe@example.com", [_pos("TQQQ", 10, 50.0)])
            await rs.create_rule(s, user, ticker="TQQQ", rule_type="strategy_signal",
                                 mode="exit", level=0, strategy_id=TREND)
            s.add(StrategySignalState(subject=user.email, strategy_id=TREND,
                                      target={"TQQQ": 1.0}, previous_target={"QQQ": 1.0},
                                      as_of="2026-08-10", flipped_at=rs._now()))
            await s.commit()
            newly = await rs.evaluate_user(s, user)
            rules = list((await s.scalars(select(TradingRule).where(
                TradingRule.subject == user.email))).all())
    finally:
        await eng.dispose()
    assert newly == []
    assert rules[0].triggered is False


# --------------------------------------------------------------------------- #
# P4.3 — drift vs cold start
# --------------------------------------------------------------------------- #
def test_the_drift_band_is_a_named_constant_not_a_magic_number():
    assert rr.SLEEVE_DRIFT_BAND_PCT == 5.0


def test_fund_sleeve_is_actionable_and_has_a_handler():
    """A kind listed as actionable with no branch in apply_recommendation would
    report 'Done -- applied.' and change nothing. That exact bug shipped once."""
    assert "fund_sleeve" in rr._ACTIONABLE_KINDS
    assert 'kind == "fund_sleeve"' in inspect.getsource(rr.apply_recommendation)
