"""B2. "Retire the overlay, hold the base" was not an operation the app had.

**The decision.** Measured on the shipped engine over 2016-09-06 → 2026-09-04,
2,514 sessions, adjusted closes:

    hold TQQQ, do nothing        40.25%/yr   vol 66.74%   maxDD 81.66%
    hold QQQ, do nothing         20.73%/yr   vol 22.56%   maxDD 35.12%
    btm_swing_dip as shipped     17.19%/yr   vol 34.35%   maxDD 40.55%

The overlay turned a 40.25% instrument into 17.19% — *below simply holding the
base* — while carrying a deeper drawdown than the base and paying ~7 points a
year to 45 round-trips. Leverage decay was not the cause; being out of the
market during the compounding was. Eran's decision, 2026-09-04: drop the rule,
keep the position.

**Why that needed two new things.** `DELETE /plan/sleeves/btm_swing_dip` retires
the whole SLEEVE — the 36% collapses into the core, the core goes 27% → ~55%,
and the book's Fixed Income target swings 7.8% → ~22%, so the app starts asking
to sell equities into BND. Dropping a *rule* and dropping an *allocation* were
the same operation, and only one of them was ever wanted. So:

  * `btm_qqq_hold` — a catalogue entry that holds the index and does nothing
    else, because there was nowhere for a retired overlay to land.
  * `swap_sleeve` — one operation that moves the size across, because the order
    is forced and the two-step has a window in it.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.main as m


def _seed(c):
    """One holding, so the book has a NAV for the sleeve to be a share of."""
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "TQQQ", "market": "NASDAQ", "asset_class": "Equities", "depth": 3,
         "spot_price": 100, "listing_price": 100, "quantity": 50, "cost_basis": 80,
         "expected_return_pct": 20, "volatility_pct": 60}]})


def test_the_catalogue_can_express_holding_the_index():
    """There was no entry that just holds something. That was the gap.

    Every other entry is a rule. This one is deliberately not, and its absence
    is the whole reason retiring an overlay and retiring an allocation were the
    same button.
    """
    from app.services import strategy_catalog

    entry = strategy_catalog.get("btm_qqq_hold")
    assert entry is not None, "btm_qqq_hold is gone; a retired overlay has nowhere to land"
    assert entry["weights"] == {"QQQ": 1.0}
    assert (entry.get("overlay") or {}).get("kind") == "buy_hold"


def test_it_declares_no_base_and_that_is_the_definition():
    """A base is where a rule goes when it is off. There is no rule.

    It is also what makes the sleeve fundable at all: `sleeve_targets` funds
    `weights` minus `base`, so an entry naming QQQ in both would ask the app to
    fund nothing and the sleeve would sit permanently empty.
    """
    from app.services import strategy_catalog
    from app.services.strategy_service import sleeve_targets

    entry = strategy_catalog.get("btm_qqq_hold")
    assert not entry.get("base"), (
        "btm_qqq_hold declares a base. It has no rule, so there is nothing for a "
        "base to mean -- and sleeve_targets would then fund none of it.")
    assert not entry.get("risk_off"), "a strategy with no rule cannot go risk-off"

    assert sleeve_targets("btm_qqq_hold", 36.05) == {"QQQ": 0.3605}


def test_the_engine_needs_no_new_overlay_to_measure_it():
    """`buy_hold` already exists, so this is measured by the same code path.

    An entry the backtest could not run would be a sleeve with no measured
    numbers, which `measured_assumptions` reports as unmeasured coverage -- a
    silent hole in the book's forward figures rather than a loud failure.
    """
    from app.services import strategy_catalog

    spec = strategy_catalog.backtestable(only=["btm_qqq_hold"])
    assert len(spec) == 1, "btm_qqq_hold is not in the backtestable pool"
    assert spec[0]["overlay"]["kind"] == "buy_hold"

    # `targets_for` dispatches buy_hold before it looks for an overlay function,
    # so a constant weight comes back rather than an abstention dict.
    import numpy as np

    from app.engines.strategy_backtest import targets_for

    px = {"QQQ": np.array([100.0, 101.0, 99.0, 103.0])}
    w = targets_for(px, spec[0])
    assert isinstance(w, list), f"the engine abstained on btm_qqq_hold: {w}"
    assert w == [{"QQQ": 1.0}] * 4, w


def test_the_sleeve_ceiling_is_derived_rather_than_typed():
    """`sleeve_count` carried "Seven is the whole backtestable() pool today".

    Then the pool became eight, which is exactly the condition that sentence
    described, and nothing would have noticed -- MAX_SLEEVES would have started
    binding, and a search that stopped because it hit a backstop reads as a
    search that converged. That distinction is the point of the whole module.
    """
    from app.services import strategy_catalog
    from app.services.sleeve_count import MAX_SLEEVES

    assert MAX_SLEEVES == len(strategy_catalog.ids()), (
        f"MAX_SLEEVES is {MAX_SLEEVES} against a pool of "
        f"{len(strategy_catalog.ids())}. Derive it, do not retype it.")


async def _with_user(own_session, fn):
    """The `own_session` idiom: a NullPool session in the test's own loop.

    Reaching for the app's pooled engine beside a TestClient passes on SQLite
    and fails on Postgres with "attached to a different loop" -- twice already,
    in two files, after the rule was written into CLAUDE.md.
    """
    from app.models.tables import User

    async with own_session() as s:
        user = (await s.execute(select(User))).scalars().first()
        out = await fn(s, user)
        await s.commit()
        return out


@pytest.mark.asyncio
async def test_a_swap_keeps_the_size_and_moves_only_the_rule(own_session):
    """The operation B2 needed, and the one the app could not express.

    Before: 36.05% to a rule that holds TQQQ. After: 36.05% to one that holds
    QQQ. The share of the book never moves, so the core is not resized and the
    book's asset-class targets do not swing.
    """
    from app.services import sleeve_service as sv
    from app.services.strategy_service import swap_sleeve

    with TestClient(m.app) as c:
        _seed(c)

        async def _go(s, user):
            await sv.add_or_resize(s, user, "btm_swing_dip", 36.05)
            before = await sv.list_sleeves(s, user)
            assert [x.strategy_id for x in before] == ["btm_swing_dip"]
            core_before = sv.remainder_pct(before)

            out = await swap_sleeve(s, user, from_id="btm_swing_dip",
                                    to_id="btm_qqq_hold")
            after = await sv.list_sleeves(s, user)
            return out, core_before, [(x.strategy_id, float(x.sleeve_pct)) for x in after], \
                sv.remainder_pct(after)

        out, core_before, after, core_after = await _with_user(own_session, _go)

    assert out["ok"] and out["swapped"], out
    assert after == [("btm_qqq_hold", 36.05)], after
    assert core_after == core_before, (
        "the core moved. A swap changes which rule runs, not how much of the "
        "book it runs -- that is the whole difference from retiring the sleeve.")
    assert out["from_tickers"] == ["TQQQ"] and out["to_tickers"] == ["QQQ"]


@pytest.mark.asyncio
async def test_a_swap_claims_no_trade(own_session):
    """It changes the target. The shares do not move, and it says so.

    `test_card_claims` forbids a card claiming a portfolio change it does not
    produce. The same rule has to hold one layer down, or a card built on this
    would inherit the claim.
    """
    from app.services import sleeve_service as sv
    from app.services.intake_service import list_positions
    from app.services.strategy_service import swap_sleeve

    with TestClient(m.app) as c:
        _seed(c)

        async def _go(s, user):
            await sv.add_or_resize(s, user, "btm_swing_dip", 36.05)
            held_before = {(p.ticker, float(p.quantity))
                           for p in await list_positions(s, user)}
            out = await swap_sleeve(s, user, from_id="btm_swing_dip",
                                    to_id="btm_qqq_hold")
            held_after = {(p.ticker, float(p.quantity))
                          for p in await list_positions(s, user)}
            return out, held_before, held_after

        out, before, after = await _with_user(own_session, _go)

    assert after == before, "a swap moved shares"
    assert "no brokerage order" in out["broker_note"].lower(), out


@pytest.mark.asyncio
async def test_a_swap_into_an_unknown_strategy_is_refused(own_session):
    """`validate` only ever asked about size and duplication.

    So a typo'd id would have been accepted as a sleeve for a strategy that does
    not exist -- sized, capped, and funded against nothing. And the refusal has
    to come BEFORE the removal, or a typo costs you the sleeve you had.
    """
    from app.services import sleeve_service as sv
    from app.services.strategy_service import swap_sleeve

    with TestClient(m.app) as c:
        _seed(c)

        async def _go(s, user):
            await sv.add_or_resize(s, user, "btm_swing_dip", 36.05)
            out = await swap_sleeve(s, user, from_id="btm_swing_dip",
                                    to_id="btm_qqq_hodl")
            after = [x.strategy_id for x in await sv.list_sleeves(s, user)]
            return out, after

        out, after = await _with_user(own_session, _go)

    assert out["ok"] is False and "catalogue" in out["error"], out
    assert after == ["btm_swing_dip"], (
        "the original sleeve was removed before the replacement was checked")


@pytest.mark.asyncio
async def test_a_swap_onto_a_sleeve_you_already_run_is_refused(own_session):
    """That is a merge: two sizes for one strategy, and no honest way to pick.

    The unique constraint would stop the second row anyway; refusing here means
    the caller gets a reason instead of an IntegrityError.
    """
    from app.services import sleeve_service as sv
    from app.services.strategy_service import swap_sleeve

    with TestClient(m.app) as c:
        _seed(c)

        async def _go(s, user):
            await sv.add_or_resize(s, user, "btm_swing_dip", 36.05)
            await sv.add_or_resize(s, user, "btm_qqq_hold", 20.0)
            out = await swap_sleeve(s, user, from_id="btm_swing_dip",
                                    to_id="btm_qqq_hold")
            after = {x.strategy_id: float(x.sleeve_pct)
                     for x in await sv.list_sleeves(s, user)}
            return out, after

        out, after = await _with_user(own_session, _go)

    assert out["ok"] is False, out
    assert "already a sleeve" in out["error"]
    assert after == {"btm_swing_dip": 36.05, "btm_qqq_hold": 20.0}, (
        "a refused swap changed the book")


@pytest.mark.asyncio
async def test_swapping_something_you_do_not_run_is_refused(own_session):
    from app.services.strategy_service import swap_sleeve

    with TestClient(m.app) as c:
        _seed(c)

        async def _go(s, user):
            return await swap_sleeve(s, user, from_id="btm_trend_soxl",
                                     to_id="btm_qqq_hold")

        out = await _with_user(own_session, _go)

    assert out["ok"] is False and "not a sleeve" in out["error"], out
