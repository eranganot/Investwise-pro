"""PR 106: "funding may only sell what the plan says you have too much of"
holds on the sleeve path, not just on Today's.

Proven 2026-10-08 (probe docs/plans/evidence-2026-10-08/probes/scratch_funding/
test_c1_sleeve_funding.py): Grow + the 60/40 core + a 10% SOXL sleeve. `/mix`
says Fixed Income is 20% held against a 32% book target, and sleeve funding sold
₪37,100 of BND as "the most overweight class (20% vs 10%)" -- measured against
the objective table instead of the book. Total drift rose 0.44 -> 0.64.
"""
from fastapi.testclient import TestClient

import app.main as m


def _p(tk, cls, qty, price=100.0, basis=99.0):
    return {"ticker": tk, "market": "NASDAQ", "asset_class": cls, "depth": 3,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": basis, "expected_return_pct": 8, "volatility_pct": 20}


def _seed(c):
    c.put("/api/v1/plan", json={"objective": "Grow"})
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        _p("V", "Equities", 200, basis=40.0), _p("SCHD", "Equities", 200, basis=40.0),
        _p("MSFT", "Equities", 200, basis=40.0), _p("VTI", "Equities", 200, basis=40.0),
        _p("BND", "Fixed Income", 200, basis=99.0)]})
    assert c.post("/api/v1/plan/core", json={"strategy_id": "bal_6040"}).status_code < 400
    assert c.post("/api/v1/strategies/btm_trend_soxl/apply?sleeve_pct=10").json().get("ok")
    mix = c.get("/api/v1/mix").json()
    return mix["current_allocation"], mix["target_allocation"]


def _assert_sells_only_overweight(funding, held, target):
    over = {cls for cls, w in held.items() if w > float(target.get(cls, 0.0) or 0.0)}
    assert over, "the book must have an over-target class for this test to mean anything"
    for s in (funding or {}).get("sells", []):
        cls = s["asset_class"]
        assert cls in over, (
            f"sold {s['ticker']} ({cls}: {held.get(cls, 0):.0%} held vs "
            f"{float(target.get(cls, 0) or 0):.0%} target) while {sorted(over)} is over target")


def test_funding_every_sleeve_sells_only_what_the_plan_says_is_overweight():
    with TestClient(m.app) as c:
        held, target = _seed(c)
        assert held["Fixed Income"] < target["Fixed Income"]       # 20% vs 32%
        res = c.post("/api/v1/plan/sleeves/fund?dry_run=true").json()
        assert res.get("ok"), res
        _assert_sells_only_overweight(res["funding"], held, target)
        # The summary never calls an under-target class the most overweight.
        assert "Fixed Income is the most overweight" not in (res.get("funding_summary") or "")


async def test_the_single_sleeve_and_the_solver_preview_agree(own_session):
    from sqlalchemy import select

    from app.models.tables import User
    from app.services import strategy_service as ss
    from app.services import target_solver as ts
    from app.services.intake_service import list_positions

    with TestClient(m.app) as c:
        held, target = _seed(c)
    async with own_session() as s:
        u = (await s.execute(select(User))).scalars().first()
        one = await ss._fund_sleeve(s, u, "btm_trend_soxl", {}, total=None,
                                    sleeve_pct=10.0, dry_run=True)
        nav = ss._snapshot(await list_positions(s, u))["nav"]
        prev = await ts._funding_preview(s, u, [{"current_pct": 0.0}], 10.0, nav)
    assert one.get("ok"), one
    _assert_sells_only_overweight(one["funding"], held, target)
    legs = prev.get("legs") or []
    assert legs and not any(x["ticker"] == "BND" for x in legs), legs


def test_a_funding_that_raises_drift_is_refused_and_names_the_class():
    """The post-trade gate, on the shape the class budget is meant to prevent."""
    from types import SimpleNamespace as NS

    from app.services.funding_service import drift_refusal
    from app.services.strategy_service import _drift_gate

    def row(tk, cls, value):
        return NS(ticker=tk, market="TASE", quantity=value, current_price=1.0, cost_basis=1.0,
                  meta={"asset_class": cls, "asset_class_source": "user"})

    rows = [row("EQ", "Equities", 80_000), row("BND", "Fixed Income", 20_000)]
    snap = {"nav": 100_000.0}
    target = {"Equities": 0.58, "Fixed Income": 0.32, "Commodities": 0.10}
    fund = {"sells": [{"ticker": "BND", "asset_class": "Fixed Income", "value_ils": 10_000.0}]}
    legs = [{"ticker": "SOXL", "buy_ils": 10_000.0}]
    gate = _drift_gate(rows, snap, fund, legs, "Grow", target)
    assert gate is not None and gate["after"] > gate["before"], gate
    assert gate["sold_at_or_under_target"] == ["Fixed Income"]
    assert "Fixed Income (20% held, 32% target)" in drift_refusal(gate)

    # The same buy funded from the over-target class is a swap: no refusal.
    swap = {"sells": [{"ticker": "EQ", "asset_class": "Equities", "value_ils": 10_000.0}]}
    assert _drift_gate(rows, snap, swap, legs, "Grow", target) is None


def test_the_gate_measures_the_whole_book_cash_included():
    """PR 106.1, the live book of 2026-10-11 (a303f9e): Equities 75.65%, Cash
    3.12%, Commodities 9.12%, Fixed Income 12.11% against a book target of
    77.22 / 0.64 / 10 / 12.14. Funding SOXL from ₪580 of cash plus ₪377 of BND.

    Measured on invested holdings only (PR 106), Fixed Income looked 12.5% --
    over target -- and the refusal could not say what it was protecting.
    """
    from types import SimpleNamespace as NS

    from app.services.strategy_service import _drift_gate

    def row(tk, cls, value):
        return NS(ticker=tk, market="TASE", quantity=value, current_price=1.0, cost_basis=1.0,
                  meta={"asset_class": cls, "asset_class_source": "user"})

    nav = 23_417.62
    rows = [row("EQ", "Equities", 0.7565 * nav), row("CO", "Commodities", 0.0912 * nav),
            row("BND", "Fixed Income", 0.1211 * nav)]
    snap = {"nav": nav}          # the rest (3.12%) is cash
    target = {"Equities": 0.7722, "Fixed Income": 0.1214, "Cash": 0.0064, "Commodities": 0.10}
    fund = {"from_cash_ils": 580.15,
            "sells": [{"ticker": "BND", "asset_class": "Fixed Income", "value_ils": 377.28}]}
    gate = _drift_gate(rows, snap, fund, [{"ticker": "SOXL", "buy_ils": 957.24}], "Grow", target)
    assert gate is not None, "selling an under-target class into an overshoot must be refused"
    assert gate["held"]["Fixed Income"] == 0.1211, gate
    assert "Fixed Income (12.11% held, 12.14% target)" in gate["reason"], gate["reason"]
