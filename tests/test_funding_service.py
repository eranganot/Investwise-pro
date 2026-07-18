"""Funding: every buy names its money source and a real size.

Cards used to say what to do but not how to pay for it or how much, so they read
as advice. Cash is spent first (down to a plan-derived floor), then the
worst-fitting holdings, ranked by plan fit rather than by what's easiest to sell.
"""
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


def test_size_purchase_respects_target_and_cap():
    # 10% NAV gap to a 30% target, but a 20% single-name cap from a 15% weight.
    assert f.size_purchase(100000, 0.20, 0.30) == 10000.0
    assert f.size_purchase(100000, 0.15, 0.30, cap=0.20) == 5000.0    # cap binds


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
    txt = f.describe_funding(fund)
    assert "from cash" in txt and "selling 10 AAA" in txt and "tax" in txt.lower()


def test_trim_ranking_puts_over_cap_names_first():
    rows = [_Pos("HUGE", "TASE", 800, 100, 60, "Equities"),   # 80% of book, over any cap
            _Pos("OK", "TASE", 20, 100, 90, "Equities")]      # 2%
    snap = _snap(82000)
    ranked = f.rank_trim_candidates(rows, snap, "Balanced", 0.20)
    assert ranked[0]["ticker"] == "HUGE"
    assert "cap" in ranked[0]["reason"]


def test_buy_funded_executes_via_the_apply_service(monkeypatch):
    """Accepting a buy_funded spec sells the funding leg and buys the target,
    exercising the real apply_recommendation path (no money spent unraised)."""
    import asyncio
    from types import SimpleNamespace as NS
    from app.providers import registry
    from app.core.database import AsyncSessionLocal
    from app.services import recommendations as rr
    from app.services.feed_service import ensure_user
    from app.services.intake_service import (
        ensure_account, ensure_entity, upsert_positions, list_positions)
    from app.schemas.intake import IntakePosition
    from app.schemas.state_machine import Market

    monkeypatch.setattr(registry, "guarded_quote",
                        lambda tk: NS(price=100.0, currency="USD"))

    async def run():
        async with AsyncSessionLocal() as s:
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
            return result, rows

    result, rows = asyncio.get_event_loop().run_until_complete(run())
    assert result and result.get("bought") == "NEWBUY"
    assert "NEWBUY" in rows
    assert float(rows["OVERWEIGHT"].quantity) < 200      # funding leg really sold
