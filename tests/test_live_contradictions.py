"""Two contradictions caught by looking at the live Today screen, not by a test.

Both are the same species: an agent that is individually correct, giving advice
that is wrong once you know what the rest of the app is doing.
"""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.tables import TradingRule
from app.schemas.intake import IntakePosition
from app.schemas.state_machine import Market
from app.services import rules_service as rs
from app.services.feed_service import ensure_user
from app.services.intake_service import (
    ensure_account, ensure_entity, upsert_positions)


def _session():
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


async def _book(s, email, positions):
    user = await ensure_user(s, email)
    entity = await ensure_entity(s, user, "Personal", "Personal")
    account = await ensure_account(s, entity, "Main")
    await upsert_positions(s, account, list(positions))
    await s.commit()
    return user


def _pos(ticker, qty, price, basis):
    return IntakePosition(ticker=ticker, market=Market.TASE, depth=2, spot_price=price,
                          listing_price=price, quantity=qty, cost_basis=basis,
                          asset_class="Equities")


@pytest.mark.asyncio
async def test_a_cap_that_has_corrected_itself_stops_nagging():
    """Live: SOXL sat at 9.66% against a 10% cap, and Today still said
    "SOXL is 10% of your portfolio - consider trimming".

    The rule had latched when the weight was briefly over, and only price alerts
    ever auto-cleared. Worse, execution_plan correctly returns None once the
    weight is back under, so the card degraded to guidance with no action -- a
    nag that no user action could clear.
    """
    eng, Session = _session()
    try:
        async with Session() as s:
            # 1 unit at 200 in a 2,000 book = 10% exactly -> breach.
            user = await _book(s, "cap_clear_probe@example.com",
                               [_pos("SOXL", 1, 200.0, 200.0), _pos("MSFT", 18, 100.0, 100.0)])
            await rs.create_rule(s, user, ticker="SOXL", rule_type="max_weight",
                                 mode="pct", level=10.0)
            fired = await rs.evaluate_user(s, user)
            assert fired, "a cap at 10% should fire on a 10% position"

            # The position drifts back under the cap on its own.
            rows = {p.ticker: p for p in await __import__(
                "app.services.intake_service", fromlist=["x"]).list_positions(s, user)}
            rows["SOXL"].current_price = 150.0        # ~7.7% of the book
            await s.commit()
            await rs.evaluate_user(s, user)

            rule = (await s.scalars(select_rule(user))).first()
            cards = await rs.triggered_rule_recs(s, user)
    finally:
        await eng.dispose()

    assert rule.triggered is False, "a standing condition must re-arm when it clears"
    assert rule.active is True, "re-arming is not retirement"
    assert cards == [], "no card for a breach that no longer exists"


def select_rule(user):
    from sqlalchemy import select
    return select(TradingRule).where(TradingRule.subject == user.email,
                                     TradingRule.rule_type == "max_weight")


def test_tax_harvest_never_targets_the_strategy_sleeve():
    """Live: the card offered to sell SOXL -- the aggressive leg of the applied
    strategy, with a max_weight cap armed on it -- to save 12 shekels of tax.

    Two agents, opposite instructions, same position, same screen. The tax
    engine cannot know the holding is there on purpose.
    """
    import inspect

    from app.services import recommendations as rr
    src = inspect.getsource(rr.build_recommendations)
    assert "sleeve_targets" in src
    # The exclusion must happen before `save` is computed, or the card would
    # advertise a saving it is no longer going to realize.
    i_excl = src.index("_held_for_strategy")
    i_save = src.index('harvest[0]["estimated_annual_tax_savings_currency"]')
    assert i_excl < i_save, "the sleeve must be excluded before the saving is quoted"
