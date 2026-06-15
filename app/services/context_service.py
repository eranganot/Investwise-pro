"""Assemble a compact, deterministic snapshot of the user's finances to ground
the AI assistant and the digest. Everything here comes from the real engines -
the LLM only ever sees these numbers, so it can't invent them."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import User
from app.services.intake_service import list_positions
from app.services.performance_service import performance
from app.services.plan_service import get_plan
from app.services.portfolio_analytics import compute_snapshot
from app.services.portfolio_risk_service import portfolio_risk
from app.services.recommendations import build_recommendations


async def gather(session: AsyncSession, user: User) -> dict:
    rows = await list_positions(session, user)
    pdicts = [{"ticker": p.ticker, "market": p.market, "quantity": float(p.quantity),
               "cost_basis": float(p.cost_basis), "current_price": float(p.current_price or 0),
               "asset_class": (p.meta or {}).get("asset_class"),
               "volatility_pct": (p.meta or {}).get("volatility_pct")} for p in rows]
    snap = compute_snapshot(pdicts) if pdicts else {"nav": 0.0}
    ctx: dict = {
        "nav_ils": round(snap.get("nav", 0.0), 2),
        "holdings": [{"ticker": d["ticker"], "qty": d["quantity"], "price": d["current_price"],
                      "value_ils": round(d["quantity"] * d["current_price"], 2),
                      "asset_class": d["asset_class"]} for d in pdicts],
    }
    try:
        recs = await build_recommendations(session, user)
        ctx["recommendations"] = [{"title": r["title"], "action": r.get("action")}
                                  for r in recs.get("recommendations", [])[:5]]
        ctx["beta_validation"] = recs.get("risk_validation")
    except Exception:
        pass
    plan = await get_plan(session, user)
    if plan:
        ctx["goal"] = {"target_ils": getattr(plan, "target_amount", None),
                       "target_date": getattr(plan, "target_date", None),
                       "objective": getattr(plan, "objective", None),
                       "risk_tolerance": getattr(plan, "risk_tolerance", None)}
    try:
        r = await portfolio_risk(session, user)
        if r.get("ok"):
            ctx["risk"] = {k: r.get(k) for k in ("annualized_volatility_pct", "annualized_return_pct",
                           "var_95_1d_pct", "cvar_95_1d_pct", "beta", "avg_correlation", "goal")}
    except Exception:
        pass
    try:
        pf = await performance(session, user)
        if pf.get("ok"):
            ctx["performance"] = {k: pf.get(k) for k in ("total_return_pct", "cagr_pct",
                                  "max_drawdown_pct", "benchmark", "benchmark_return_pct", "excess_return_pct")}
    except Exception:
        pass
    return ctx
