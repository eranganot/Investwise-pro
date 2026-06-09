"""Unified, actionable recommendations for the Today view (what to do + how)."""
from __future__ import annotations

import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from app.engines.allocation_engine import AllocationEngine
from app.models.tables import User
from app.services.allocation_mix import OBJ_TARGET, current_mix
from app.services.intake_service import list_positions
from app.services.plan_service import effective_caps, get_plan, plan_settings
from app.services.portfolio_analytics import compute_snapshot, tax_opportunities

_SEV = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def _rid(*parts) -> str:
    return "rec_" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:6]


def _ils(x) -> str:
    return f"₪{round(x):,}"


async def build_recommendations(session: AsyncSession, user: User) -> dict:
    rows = await list_positions(session, user)
    if not rows:
        return {"count": 0, "recommendations": [], "message": "Add holdings to get recommendations."}
    pdicts = [{"ticker": p.ticker, "market": p.market, "quantity": float(p.quantity),
               "cost_basis": float(p.cost_basis), "current_price": float(p.current_price or 0),
               "volatility_pct": (p.meta or {}).get("volatility_pct"),
               "liquidity_score": (p.meta or {}).get("liquidity_score")} for p in rows]
    snap = compute_snapshot(pdicts)
    nav = snap["nav"]
    plan = await get_plan(session, user)
    cap = effective_caps(plan)["concentration_cap"]
    objective = plan.objective if plan else "Balanced"
    recs: list[dict] = []

    # 1) Concentration trim
    if snap["max_weight"] > cap and nav:
        tk = max(snap["exposure_ticker"], key=snap["exposure_ticker"].get)
        w = snap["exposure_ticker"][tk]
        price = next((float(r.current_price or 0) for r in rows if r.ticker == tk), 0)
        trim = (w - cap) * nav
        shares = int(trim / price) if price else 0
        recs.append({"id": _rid("trim", tk), "dimension": "diversification", "severity": "HIGH",
                     "title": f"Trim {tk}",
                     "action": f"Sell about {_ils(trim)} of {tk} (~{shares} shares) to bring it from "
                               f"{w:.0%} down to your {cap:.0%} limit.",
                     "how": ["Open your brokerage account",
                             f"Place a SELL order for ~{shares} {tk} shares (~{_ils(trim)})",
                             "Reinvest the proceeds across your other holdings or your plan's target mix"],
                     "est_amount": round(trim, 2)})

    # 2) Tax-loss harvesting
    tx = tax_opportunities(pdicts)
    harvest = [o for o in tx["opportunities"] if o["trigger"] == "CAPITAL_LOSS_HARVESTING"]
    if harvest:
        losers = [r.ticker for r in rows if float(r.current_price or 0) < float(r.cost_basis)]
        save = harvest[0]["estimated_annual_tax_savings_currency"]
        recs.append({"id": _rid("tax"), "dimension": "tax",
                     "severity": "CRITICAL" if save > 0 else "MEDIUM",
                     "title": "Harvest a tax loss",
                     "action": f"Sell your losing position(s)"
                               f"{' (' + ', '.join(losers) + ')' if losers else ''} to realize the loss "
                               f"and save about {_ils(save)} in tax this year.",
                     "how": ["Sell the position(s) currently below what you paid",
                             "The realized loss offsets taxable gains, lowering your tax bill",
                             "If you still believe in them, re-buy after the wash-sale window"],
                     "est_amount": save})

    # 3) Rebalance toward the plan's objective
    mix, _ = current_mix(rows)
    target = OBJ_TARGET.get(objective, OBJ_TARGET["Balanced"])
    report = AllocationEngine().compute(target_allocation=target, current_allocation=mix, nav=nav)
    for a in report.rebalance_actions[:2]:
        recs.append({"id": _rid("rebal", a.asset_class), "dimension": "allocation", "severity": "MEDIUM",
                     "title": f"{a.action_type.title()} {a.asset_class}",
                     "action": f"{a.action_type.title()} about {_ils(a.estimated_trade_value_currency)} of "
                               f"{a.asset_class} to move toward your {objective} target "
                               f"({target.get(a.asset_class, 0):.0%}).",
                     "how": [f"{a.action_type.title()} {a.asset_class} by ~{_ils(a.estimated_trade_value_currency)}",
                             f"After tax & costs that's about {_ils(a.net_trade_value_currency)} moved",
                             "This nudges your mix back in line with your plan"],
                     "est_amount": a.net_trade_value_currency})

    recs.sort(key=lambda r: _SEV.get(r["severity"], 9))
    return {"count": len(recs), "objective": objective, "recommendations": recs[:6]}
