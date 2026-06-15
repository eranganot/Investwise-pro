"""Apply a strategy (preset + rebalance trades) and load its model basket."""
from __future__ import annotations


from sqlalchemy.ext.asyncio import AsyncSession

from app.engines.allocation_engine import AllocationEngine
from app.models.tables import User
from app.providers.registry import guarded_quote
from app.schemas.intake import IntakePosition
from app.schemas.state_machine import Market
from app.services import strategies as cat
from app.services.allocation_mix import current_mix
from app.services.intake_service import (
    ensure_account, ensure_entity, list_positions, upsert_positions)
from app.services.plan_service import upsert_plan
from app.services.portfolio_analytics import compute_snapshot


def _nav(rows) -> float:
    return compute_snapshot([{"ticker": p.ticker, "market": p.market, "quantity": float(p.quantity),
                              "cost_basis": float(p.cost_basis), "current_price": float(p.current_price or 0)}
                             for p in rows])["nav"] if rows else 0.0


async def apply_strategy(session: AsyncSession, user: User, strategy_id: str) -> dict:
    s = cat.get(strategy_id)
    if not s:
        return {"ok": False, "error": "unknown strategy"}
    # preset the plan
    await upsert_plan(session, user, objective=s["objective"], risk_tolerance=s["risk_tolerance"],
                      preferred_depth=s.get("preferred_depth"), strategy=strategy_id)
    await session.commit()
    # rebalance trades toward the strategy's target allocation
    rows = await list_positions(session, user)
    nav = _nav(rows)
    actions = []
    if nav > 0:
        mix, _ = current_mix(rows)
        report = AllocationEngine().compute(target_allocation=s["target_allocation"],
                                            current_allocation=mix, nav=nav)
        actions = [a.model_dump() for a in report.rebalance_actions]
    return {"ok": True, "strategy": s, "nav": round(nav, 2), "rebalance_actions": actions}


async def load_basket(session: AsyncSession, user: User, strategy_id: str,
                      total: float | None = None) -> dict:
    s = cat.get(strategy_id)
    if not s:
        return {"ok": False, "error": "unknown strategy"}
    rows = await list_positions(session, user)
    budget = total if (total and total > 0) else (_nav(rows) or 10000.0)

    # price the basket, then size by weight / price
    positions: list[IntakePosition] = []
    priced = []
    for ticker, weight in s["basket"]:
        try:
            price = float(guarded_quote(ticker).price)
        except Exception:
            price = 0.0
        if price <= 0:
            continue
        qty = (weight * budget) / price
        positions.append(IntakePosition(
            ticker=ticker, market=Market.NASDAQ, depth=s.get("preferred_depth") or 2,
            spot_price=price, listing_price=price, quantity=qty, cost_basis=price,
            asset_class=cat.ticker_asset_class(ticker, s)))
        priced.append({"ticker": ticker, "weight": weight, "price": price,
                       "value": round(qty * price, 2)})
    if not positions:
        return {"ok": False, "error": "could not price the basket"}

    # full replace: delete existing holdings, then insert the basket
    for p in await list_positions(session, user):
        await session.delete(p)
    await session.flush()
    entity = await ensure_entity(session, user, "Personal", "Personal")
    account = await ensure_account(session, entity, "Main")
    await upsert_positions(session, account, positions)
    await session.commit()
    return {"ok": True, "strategy_id": strategy_id, "budget": round(budget, 2),
            "loaded": priced, "count": len(priced)}
