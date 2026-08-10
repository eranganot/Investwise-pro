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


@pytest.mark.asyncio
async def test_today_clears_a_corrected_cap_without_waiting_for_the_price_job():
    """The first fix only cleared the flag inside evaluate_user, which runs in the
    30-minute price job -- so Today kept showing the stale card until the job
    happened to run. Observed live: the card was still there after deploy.

    The card builder had the answer all along and threw it away: it called
    _evaluate and discarded the `hit` boolean, rendering purely from the latch.
    """
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "cap_today_probe@example.com",
                               [_pos("SOXL", 1, 200.0, 200.0), _pos("MSFT", 18, 100.0, 100.0)])
            rule = await rs.create_rule(s, user, ticker="SOXL", rule_type="max_weight",
                                        mode="pct", level=10.0)
            # Latch it exactly as a past breach would have, then drift back under
            # the cap -- WITHOUT running evaluate_user, i.e. no price job.
            rule.triggered = True
            await s.commit()
            from app.services.intake_service import list_positions
            rows = {p.ticker: p for p in await list_positions(s, user)}
            rows["SOXL"].current_price = 150.0
            await s.commit()

            cards = await rs.triggered_rule_recs(s, user)
            again = await rs.triggered_rule_recs(s, user)
            fresh = (await s.scalars(select_rule(user))).first()
    finally:
        await eng.dispose()

    assert cards == [], "Today must not show a breach that has already corrected"
    assert again == [], "and it must stay cleared, not flicker back on the next load"
    assert fresh.triggered is False and fresh.active is True


def select_rule(user):
    from sqlalchemy import select
    return select(TradingRule).where(TradingRule.subject == user.email,
                                     TradingRule.rule_type == "max_weight")


def test_a_trivial_tax_saving_is_not_flagged_important():
    """Live: a 12 shekel saving on a 21,405 shekel book was severity CRITICAL —
    the level _SEV reserves for a firing stop-loss — so it sorted above
    everything and wore an "Important" badge, on an executable card that sells a
    whole position.

    Severity now scales with materiality against the app's own threshold
    (MIN_TRADE_ILS, what it already calls too small to be worth the friction),
    and nothing in this card can reach CRITICAL.
    """
    import inspect

    from app.services import recommendations as rr
    src = inspect.getsource(rr.build_recommendations)
    tax = src[src.index("# 2) Tax-loss harvesting"):src.index("# 3) Rebalance")]
    # Strip comments: the explanation of why this is not CRITICAL naturally
    # contains the word, and the first version of this test failed on its own
    # prose rather than on the code.
    code = "\n".join(ln.split("#", 1)[0] for ln in tax.splitlines())

    assert '"CRITICAL" if save > 0' not in code, "any saving must not be CRITICAL"
    assert "CRITICAL" not in code, "an optional tax optimisation is never critical"
    assert "_MIN_TRADE" in code, "severity should key off the app's materiality unit"
    # And it must say what the trade costs, not only what it saves.
    assert "_cost_note" in code


def test_the_cost_note_is_ils_normalised():
    """Shipped and caught on the screen: the note read "selling ₪667 of holdings"
    for a position worth ₪2,001. current_price x quantity is the position's
    NATIVE currency — $87.21 x 7.673 = $669 — printed with a shekel sign.

    The snapshot's weights are already FX-converted, so the value comes from
    there. Same class as the two FX bugs this app has already been bitten by.
    """
    import inspect

    from app.services import recommendations as rr
    src = inspect.getsource(rr.build_recommendations)
    tax = src[src.index("# 2) Tax-loss harvesting"):src.index("# 3) Rebalance")]
    code = "\n".join(ln.split("#", 1)[0] for ln in tax.splitlines())
    assert "_sell_value" in code
    assert "exposure_ticker" in code, "the sell value must come from the FX-normalised snapshot"
    assert "float(r.current_price or 0) * float(r.quantity)" not in code


def test_a_max_weight_rule_reports_a_weight_not_a_price():
    """Live: the Rules tab rendered "Max weight 10% -> 10.00 ... now 137.61" —
    SOXL's dollar price against a percent-of-portfolio cap. It reads as a
    thirteenfold breach when the position is at 9.7% of a 10% cap. The number
    was never wrong, it was never comparable.
    """
    import inspect

    from app.services import rules_service as rsvc
    src = inspect.getsource(rsvc.list_rules)
    assert '"unit"' in src and '"current"' in src
    assert "pct_of_portfolio" in src
    # And the latch must be distinguishable from a live breach.
    assert '"breached_now"' in src


@pytest.mark.asyncio
async def test_rules_on_a_position_you_no_longer_hold_are_deleted():
    """Asked for three times: if the holding is gone, the rule goes.

    Two earlier attempts only *retired* them (active=False) and left them in the
    list, which is not what "remove" means -- the Rules tab still showed four
    "retired" rows each for TQQQ, META and AMZN.

    Deleting is also the safer end state. A stop-loss on AMZN at 222.58, set
    months ago against a price that has moved since, would otherwise sit dormant
    and go live the instant the position is re-bought, firing at a level nobody
    chose for today's market. Gone means you set a fresh one.
    """
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "purge_probe@example.com",
                               [_pos("MSFT", 10, 100.0, 100.0)])
            await rs.create_rule(s, user, ticker="MSFT", rule_type="stop_loss",
                                 mode="price", level=80.0)
            await rs.create_rule(s, user, ticker="AMZN", rule_type="stop_loss",
                                 mode="price", level=222.58)
            await rs.create_rule(s, user, ticker="META", rule_type="max_weight",
                                 mode="pct", level=35.0)

            removed = await rs.purge_orphan_rules(s, user)
            listed = await rs.list_rules(s, user)
    finally:
        await eng.dispose()

    assert {r["ticker"] for r in removed} == {"AMZN", "META"}
    assert [r["ticker"] for r in listed] == ["MSFT"], "only live rules remain"


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

@pytest.mark.asyncio
async def test_list_rules_retires_on_read_not_only_in_the_price_job():
    """Fixed in the evaluator TWICE, and the Rules tab still showed "armed"
    rules on TQQQ, META and AMZN both times -- because evaluate_user runs in the
    30-minute price job while the tab renders from the DB.

    Same lesson as the max_weight latch, not applied here the first time: a
    screen that must be self-consistent has to reconcile when it renders.
    """
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await _book(s, "retire_on_read@example.com",
                               [_pos("MSFT", 10, 100.0, 100.0)])
            await rs.create_rule(s, user, ticker="AMZN", rule_type="stop_loss",
                                 mode="price", level=222.58)
            await rs.create_rule(s, user, ticker="MSFT", rule_type="stop_loss",
                                 mode="price", level=80.0)
            # NO evaluate_user call -- straight to the read path the tab uses.
            listed = {r["ticker"]: r for r in await rs.list_rules(s, user)}
            again = {r["ticker"]: r for r in await rs.list_rules(s, user)}
    finally:
        await eng.dispose()

    assert "AMZN" not in listed, "a rule on an unheld position must be DELETED, not listed"
    assert listed["MSFT"]["active"] is True, "a held position keeps its rules"
    assert "AMZN" not in again, "and it stays gone"

@pytest.mark.asyncio
async def test_a_purge_never_runs_against_an_empty_book():
    """The guard that matters. An empty book and a failed position read are
    indistinguishable from inside rules_service, and wiping every rule on a
    transient database hiccup is unrecoverable. With no holdings, do nothing.
    """
    eng, Session = _session()
    try:
        async with Session() as s:
            user = await ensure_user(s, "purge_guard@example.com")
            entity = await ensure_entity(s, user, "Personal", "Personal")
            await ensure_account(s, entity, "Main")
            await s.commit()
            # Rules, but not a single holding.
            await rs.create_rule(s, user, ticker="AMZN", rule_type="stop_loss",
                                 mode="price", level=222.58)
            await rs.create_rule(s, user, ticker="META", rule_type="max_weight",
                                 mode="pct", level=35.0)
            removed = await rs.purge_orphan_rules(s, user)
            still = await rs.list_rules(s, user)
    finally:
        await eng.dispose()

    assert removed == [], "an empty index must delete nothing"
    assert len(still) == 2, "the rules must survive an empty/failed position read"
