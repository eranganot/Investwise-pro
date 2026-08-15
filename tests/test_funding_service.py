"""Funding: every buy names its money source and a real size.

Cards used to say what to do but not how to pay for it or how much, so they read
as advice. Cash is spent first (down to a plan-derived floor), then the
worst-fitting holdings, ranked by plan fit rather than by what's easiest to sell.
"""
import pytest
from types import SimpleNamespace

from app.services import funding_service as f


def _snap(nav):
    return {"nav": nav, "exposure_ticker": {}}


class _Pos:
    def __init__(self, ticker, market, qty, price, basis, asset_class=None):
        self.ticker, self.market = ticker, market
        self.quantity, self.current_price, self.cost_basis = qty, price, basis
        self.meta = {"asset_class": asset_class} if asset_class else {}


def test_cash_floor_scales_with_objective():
    assert f.cash_floor_pct("Preserve") > f.cash_floor_pct("Grow")
    assert f.cash_floor_ils(100000, "Grow") == 3000.0        # 3% of NAV
    assert f.cash_floor_ils(100000, "Preserve") == 10000.0   # 10%


def test_cash_floor_override_is_respected():
    plan = SimpleNamespace(cash_floor_pct=0.2)
    assert f.cash_floor_pct("Grow", plan) == 0.2


def test_spendable_cash_is_above_the_floor_only():
    # ₪5,000 cash on a ₪100k Grow book: floor 3% = ₪3,000, so ₪2,000 spendable.
    assert f.spendable_cash(5000, 100000, "Grow") == 2000.0
    assert f.spendable_cash(2000, 100000, "Grow") == 0.0     # under floor -> nothing


def test_a_class_at_or_over_target_has_no_gap():
    """The whole defect in one assertion. Equities at 97% against an 80% target
    is not a buying opportunity, and the function that answers "how much does the
    plan want here" now says zero instead of being handed a ticker weight."""
    assert f.class_gap_ils(100000, {"Equities": 0.60}, "Equities", 0.80) == 20000.0
    assert f.class_gap_ils(100000, {"Equities": 0.97}, "Equities", 0.80) == 0.0
    assert f.class_gap_ils(100000, {"Equities": 0.80}, "Equities", 0.80) == 0.0


def test_name_room_is_capped_headroom_not_a_plan_gap():
    """Room under the cap is a ceiling. It is never a reason to buy -- which is
    exactly what it was used as when a 0%-weight name got sized to the cap."""
    assert f.name_room_ils(100000, 0.15, 0.40) == 25000.0
    assert f.name_room_ils(100000, 0.45, 0.40) == 0.0        # already over the cap


def test_size_purchase_is_gone():
    """`size_purchase(nav, current_weight, target_weight, cap)` took two floats
    both named "...weight", so a TICKER weight passed where an ASSET CLASS target
    belonged read fine and shipped. The name must not come back."""
    assert not hasattr(f, "size_purchase")


def test_funding_prefers_cash_then_worst_fit_holding():
    rows = [_Pos("BIG", "TASE", 100, 100, 50, "Equities"),     # ₪10k, big winner
            _Pos("SMALL", "TASE", 5, 100, 100, "Equities")]    # ₪500
    snap = _snap(10500)
    # need ₪4,000; ₪1,000 spendable cash on a Balanced book (floor 5% of 10.5k = 525)
    fund = f.plan_funding(rows, snap, None, "Balanced", 0.30, 4000, cash_ils=1000)
    assert fund["from_cash_ils"] > 0
    assert fund["sells"]                                       # the rest from a sale
    assert fund["sells"][0]["ticker"] == "BIG"                # the overweight name


def test_funding_reports_shortfall_when_it_cannot_cover():
    rows = [_Pos("ONLY", "TASE", 3, 100, 100, "Equities")]    # only ₪300 to sell
    fund = f.plan_funding(rows, _snap(300), None, "Balanced", 0.30, 5000, cash_ils=0)
    assert fund["shortfall_ils"] > 0


def test_describe_funding_is_plain_language():
    fund = {"from_cash_ils": 1000, "sells": [
        {"ticker": "AAA", "shares": 10, "value_ils": 2000, "reason": "overweight"}],
        "tax_ils": 150, "shortfall_ils": 0}
    txt = f.describe_funding(fund, None)
    assert "from cash" in txt and "selling 10 AAA" in txt and "tax" in txt.lower()


def test_describe_funding_requires_a_buying_class():
    """It had a default, Today never passed it, and the honesty clause below
    silently never fired for months. A missing argument is now an error at the
    call site rather than a missing sentence in production prose."""
    with pytest.raises(TypeError):
        f.describe_funding({"from_cash_ils": 100, "sells": []})


def test_selling_a_class_to_buy_it_back_says_the_weight_does_not_move():
    """Ported down from tests/test_c3_funding.py:353 so it guards the SHARED
    function rather than one consumer's route. Guarding it at the route was how a
    second consumer shipped the same bug through a different door."""
    fund = {"from_cash_ils": 0, "sells": [
        {"ticker": "SCHD", "shares": 34, "value_ils": 3490, "asset_class": "Equities",
         "reason": "cheapest way to raise it"}], "tax_ils": 27, "shortfall_ils": 0}
    txt = f.describe_funding(fund, "Equities")
    assert "does not change your Equities weight" in txt
    assert "swaps which equities you hold" in txt


def test_a_cross_class_sale_makes_no_such_claim():
    """The control case. Selling equities to buy commodities DOES move both
    weights, so the disclaimer must stay silent -- it is not boilerplate."""
    fund = {"from_cash_ils": 0, "sells": [
        {"ticker": "SCHD", "shares": 34, "value_ils": 3490, "asset_class": "Equities",
         "reason": "r"}], "tax_ils": 0, "shortfall_ils": 0}
    assert "does not change your" not in f.describe_funding(fund, "Commodities")


def test_a_cash_funded_purchase_makes_no_claim_about_weights():
    fund = {"from_cash_ils": 5000, "sells": [], "tax_ils": 0, "shortfall_ils": 0}
    assert "does not change your" not in f.describe_funding(fund, "Equities")


def test_trim_ranking_puts_over_cap_names_first():
    rows = [_Pos("HUGE", "TASE", 800, 100, 60, "Equities"),   # 80% of book, over any cap
            _Pos("OK", "TASE", 20, 100, 90, "Equities")]      # 2%
    snap = _snap(82000)
    ranked = f.rank_trim_candidates(rows, snap, "Balanced", 0.20)
    assert ranked[0]["ticker"] == "HUGE"
    assert "cap" in ranked[0]["reason"]


@pytest.mark.asyncio
async def test_buy_funded_executes_via_the_apply_service(monkeypatch):
    """Accepting a buy_funded spec sells the funding leg and buys the target,
    exercising the real apply_recommendation path (no money spent unraised).

    Uses a throwaway NullPool engine in this test's own event loop, per the
    Postgres isolation rule in CLAUDE.md -- borrowing the app's shared async
    engine across loops makes asyncpg reject the connection.
    """
    from types import SimpleNamespace as NS
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from app.core.config import get_settings
    from app.providers import registry
    from app.services import recommendations as rr
    from app.services.feed_service import ensure_user
    from app.services.intake_service import (
        ensure_account, ensure_entity, upsert_positions, list_positions)
    from app.schemas.intake import IntakePosition
    from app.schemas.state_machine import Market

    monkeypatch.setattr(registry, "guarded_quote",
                        lambda tk: NS(price=100.0, currency="USD"))

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with Session() as s:
            user = await ensure_user(s, "fund_probe@example.com")
            entity = await ensure_entity(s, user, "Personal", "Personal")
            account = await ensure_account(s, entity, "Main")
            await upsert_positions(s, account, [IntakePosition(
                ticker="OVERWEIGHT", market=Market.TASE, depth=2, spot_price=100,
                listing_price=100, quantity=200, cost_basis=100, asset_class="Equities")])
            await s.commit()
            spec = {"kind": "buy_funded", "ticker": "NEWBUY", "market": "NYSE",
                    "asset_class": "Commodities", "amount_ils": 2000, "from_cash_ils": 0.0,
                    "sells": [{"ticker": "OVERWEIGHT", "market": "TASE", "shares": 20,
                               "value_ils": 2000, "tax_ils": 0, "reason": "overweight"}]}
            probe = {"id": "rec_probe", "title": "probe", "apply": spec}
            orig = rr.build_recommendations
            async def fake_build(session, u):
                return {"recommendations": [probe]}
            rr.build_recommendations = fake_build
            try:
                result = await rr.apply_recommendation(s, user, "rec_probe")
            finally:
                rr.build_recommendations = orig
            rows = {p.ticker: p for p in await list_positions(s, user)}
    finally:
        await eng.dispose()

    assert result and result.get("bought") == "NEWBUY"
    assert "NEWBUY" in rows
    assert float(rows["OVERWEIGHT"].quantity) < 200      # funding leg really sold


# --------------------------------------------------------------------------- #
# propose_funded_buy -- the only way to build a funded buy
#
# The August contradiction in one book: Equities at 97% against an 80% Grow
# target, three cards proposing to BUY equities funded by SELLING equities, each
# claiming to move the class toward target. Every assertion below is one of the
# ways that card can no longer be constructed.
# --------------------------------------------------------------------------- #
def _overweight_book():
    """97% equities against an 80% Grow target — the live book, scaled down."""
    return [_Pos("TQQQ", "TASE", 300, 100, 60, "Equities"),
            _Pos("SOXL", "TASE", 300, 100, 70, "Equities"),
            _Pos("SCHD", "TASE", 400, 100, 95, "Equities")]


def _underweight_book():
    """55% equities against the same 80% target — the plan genuinely wants more."""
    return [_Pos("SCHD", "TASE", 550, 100, 95, "Equities"),
            _Pos("BND", "TASE", 450, 100, 98, "Fixed Income")]


def test_a_buy_into_an_overweight_class_is_refused_outright():
    """D1. The gate used to ask "does the plan hold this class at all", so a
    class 17 points OVER target passed it."""
    rows = _overweight_book()
    assert f.propose_funded_buy(
        rows=rows, snap=_snap(100000), plan=None, objective="Grow", cap=0.40,
        ticker="AVUV", buying_class="Equities", cash_ils=0.0, exclude=set()) is None


def test_the_same_buy_survives_as_a_swap_when_the_caller_opts_in():
    """Decision of 2026-08-15: the trade is legitimate — shedding 3x leveraged
    exposure de-risks a 97%-equity book — so it is kept and relabelled, not
    suppressed."""
    buy = f.propose_funded_buy(
        rows=_overweight_book(), snap=_snap(100000), plan=None, objective="Grow",
        cap=0.40, ticker="AVUV", buying_class="Equities", cash_ils=0.0,
        exclude=set(), allow_within_class_swap=True)
    assert buy is not None
    assert buy.kind == "within_class_swap"
    assert buy.is_swap


def test_a_swap_cannot_narrate_itself_as_progress():
    """D3 + D4 together. The caller opts into the TRADE; it never opts into the
    LABEL. Whatever it asked for, a trade that moves no weight says so."""
    buy = f.propose_funded_buy(
        rows=_overweight_book(), snap=_snap(100000), plan=None, objective="Grow",
        cap=0.40, ticker="AVUV", buying_class="Equities", cash_ils=0.0,
        exclude=set(), allow_within_class_swap=True)
    assert "closer to target" not in buy.impact
    assert "toward" not in buy.impact.lower()
    assert "Does not move your Equities weight" in buy.impact
    assert "does not change your Equities weight" in buy.summary
    # And the arithmetic agrees with the sentence.
    assert abs(buy.class_after - buy.class_before) < f.MIN_CLAIMABLE_MOVE


def test_a_real_gap_produces_a_real_claim_with_the_numbers_behind_it():
    """The positive case. When the class IS underweight and the money comes from
    somewhere else, the card may claim a move -- and the claim is rendered from
    the simulated post-trade mix, not from the trade size."""
    buy = f.propose_funded_buy(
        rows=_underweight_book(), snap=_snap(100000), plan=None, objective="Grow",
        cap=0.40, ticker="VTI", buying_class="Equities", cash_ils=20000.0,
        exclude=set())
    assert buy is not None and buy.kind == "toward_target"
    assert buy.class_after > buy.class_before
    assert f"{buy.class_before:.0%}" in buy.impact and f"{buy.class_after:.0%}" in buy.impact


def test_the_claimed_move_is_the_move_that_actually_happens():
    """The invariant, at unit level: re-derive the post-trade weight from the
    proposal's own legs and assert it matches what the card claims."""
    buy = f.propose_funded_buy(
        rows=_underweight_book(), snap=_snap(100000), plan=None, objective="Grow",
        cap=0.40, ticker="VTI", buying_class="Equities", cash_ils=20000.0,
        exclude=set())
    by_class = {"Equities": 55000.0, "Fixed Income": 45000.0}
    by_class["Equities"] += buy.amount_ils
    for s in buy.fund["sells"]:
        by_class[s["asset_class"]] -= s["value_ils"]
    expected = by_class["Equities"] / sum(by_class.values())
    assert buy.class_after == pytest.approx(expected, abs=0.001)


def test_a_buy_is_never_sized_from_room_under_the_cap_alone():
    """D2. `size_purchase(nav, ticker_weight, class_target, cap)` sized a name the
    book barely held against the WHOLE 80% equities target, clipped only by the
    40% cap — an 8,170 "plan gap" no plan asked for. The size may never exceed
    what the class is actually short."""
    buy = f.propose_funded_buy(
        rows=_underweight_book(), snap=_snap(100000), plan=None, objective="Grow",
        cap=0.40, ticker="VTI", buying_class="Equities", cash_ils=100000.0,
        exclude=set())
    gap = f.class_gap_ils(100000, {"Equities": 0.55}, "Equities", 0.80)
    assert buy.amount_ils <= gap + 1.0        # 25,000, not the 40% cap headroom


def test_a_purchase_the_book_cannot_pay_for_is_not_a_recommendation():
    buy = f.propose_funded_buy(
        rows=[_Pos("TINY", "TASE", 1, 100, 100, "Equities")], snap=_snap(100),
        plan=None, objective="Grow", cap=0.40, ticker="VTI",
        buying_class="Equities", cash_ils=0.0, exclude=set())
    assert buy is None


def test_the_funded_amount_is_what_the_legs_actually_raise():
    """D6. Funding used to fill a GROSS target and subtract tax at the end, so
    every card came back short by the tax plus the sub-minimum residual —
    'still leaves ₪430 short' on a plan that was otherwise complete."""
    buy = f.propose_funded_buy(
        rows=_underweight_book(), snap=_snap(100000), plan=None, objective="Grow",
        cap=0.40, ticker="VTI", buying_class="Equities", cash_ils=0.0,
        exclude=set(), allow_within_class_swap=True)
    if buy is not None:
        assert buy.fund["shortfall_ils"] < f.MIN_TRADE_ILS
        assert "short" not in buy.summary


def test_the_ticker_being_bought_is_never_a_funding_source():
    buy = f.propose_funded_buy(
        rows=_overweight_book(), snap=_snap(100000), plan=None, objective="Grow",
        cap=0.40, ticker="SCHD", buying_class="Equities", cash_ils=0.0,
        exclude=set(), allow_within_class_swap=True)
    if buy is not None:
        assert "SCHD" not in {s["ticker"] for s in buy.fund["sells"]}


def test_an_overweight_class_is_not_a_licence_to_sell_it_three_times():
    """`trimmable_ils` is per-candidate against a mix that never moves, so a class
    40% over target authorised selling 40% of NAV out of EVERY holding in it —
    the same overweight spent once per position. On a three-position book that is
    a plan to liquidate 90% of it."""
    rows = _overweight_book()          # Equities 100% vs an 80% Grow target
    snap = _snap(100000)
    fund = f.plan_funding(rows, snap, None, "Grow", 0.40, 90000, cash_ils=0.0)
    sold = sum(s["value_ils"] for s in fund["sells"])
    # 20 points of overweight on a 100k book is 20k of authorised selling, once.
    assert sold <= 20000 + 1000, f"raised {sold:,.0f} against a 20,000 budget"
    assert fund["shortfall_ils"] > 0, "and it must say it fell short, not pretend"
