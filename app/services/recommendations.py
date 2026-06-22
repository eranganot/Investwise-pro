"""Unified, actionable recommendations for the Today view (what to do + how)."""
from __future__ import annotations

import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from app.engines.allocation_engine import AllocationEngine
from app.models.tables import User
from app.services.allocation_mix import OBJ_TARGET, classify, current_mix
from app.services import strategies as _strat
from app.services.audit_trail import audit_for, f
from app.services.audit_trail import f as _fml  # alias: 'f' is shadowed by Fundamentals locals below
from app.agents.fee_agent import FeeAgent
from app.engines.backtest_engine import BacktestEngine
from app.services.intake_service import delete_position, list_positions, update_position
from app.services.plan_service import effective_caps, get_plan, upsert_plan
from app.services.portfolio_analytics import compute_snapshot, tax_opportunities

CLASS_ETF = {"Equities": "VTI", "Fixed Income": "BND", "Cash": "BIL",
             "Commodities": "DBC", "Real Estate": "VNQ", "Alternatives": "BTAL"}

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
               "liquidity_score": (p.meta or {}).get("liquidity_score"),
               "asset_class": (p.meta or {}).get("asset_class"),
               "expense_ratio_pct": (p.meta or {}).get("expense_ratio_pct")} for p in rows]
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
                     "est_amount": round(trim, 2),
                     "apply": {"kind": "trim", "ticker": tk, "shares": shares}})
        recs[-1]["audit_trail"] = audit_for("diversification",
            raw_data={"ticker": tk, "weight": round(w, 4), "concentration_cap": round(cap, 4),
                      "nav": round(nav, 2), "price": round(price, 2)},
            formulas=[
                f("Position weight", "weight = position_value / NAV", result=f"{w:.0%}"),
                f("Trim amount", "trim = (weight - cap) * NAV",
                  substituted=f"({w:.4f} - {cap:.4f}) x {nav:,.0f}", result=f"₪{trim:,.0f}"),
                f("Shares to sell", "shares = trim / price",
                  substituted=f"{trim:,.0f} / {price:,.2f}", result=str(shares))])

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
                     "est_amount": save,
                     "apply": {"kind": "sell_losers", "tickers": losers}})
        recs[-1]["audit_trail"] = audit_for("tax",
            raw_data={"losing_tickers": losers, "estimated_annual_tax_savings": round(save, 2)},
            formulas=[
                f("Tax saved", "tax_saved = realized_loss x CGT_rate", result=f"₪{save:,.0f}")])

    # 3) Rebalance toward the plan's objective
    mix, _ = current_mix(rows)
    _sid = getattr(plan, "strategy", None)
    _s = _strat.get(_sid) if _sid else None
    target = (_s["target_allocation"] if _s else OBJ_TARGET.get(objective, OBJ_TARGET["Balanced"]))
    report = AllocationEngine().compute(target_allocation=target, current_allocation=mix, nav=nav)
    for a in report.rebalance_actions[:2]:
        recs.append({"id": _rid("rebal", a.asset_class), "dimension": "allocation", "severity": "MEDIUM",
                     "title": f"{a.action_type.title()} {a.asset_class}",
                     "action": f"{a.action_type.title()} about {_ils(a.estimated_trade_value_currency)} of "
                               f"{a.asset_class} to move toward your {objective} target "
                               f"({target.get(a.asset_class, 0):.0%}).",
                     "how": [f"{a.action_type.title()} {a.asset_class} by ~{_ils(a.estimated_trade_value_currency)}"
                             + (f" — e.g. {CLASS_ETF[a.asset_class]}" if a.asset_class in CLASS_ETF else ""),
                             f"After tax & costs that's about {_ils(a.net_trade_value_currency)} moved",
                             "This nudges your mix back in line with your plan"],
                     "est_amount": a.net_trade_value_currency,
                     "apply": {"kind": "rebalance_to_objective"}})
        recs[-1]["audit_trail"] = audit_for("allocation",
            raw_data={"asset_class": a.asset_class, "current_weight": round(mix.get(a.asset_class, 0.0), 4),
                      "target_weight": round(target.get(a.asset_class, 0.0), 4), "nav": round(nav, 2)},
            formulas=[
                f("Drift", "drift = current_weight - target_weight",
                  substituted=f"{mix.get(a.asset_class,0.0):.4f} - {target.get(a.asset_class,0.0):.4f}",
                  result=f"{mix.get(a.asset_class,0.0)-target.get(a.asset_class,0.0):+.1%}"),
                f("Gross trade", "trade = |drift| x NAV", result=f"₪{a.estimated_trade_value_currency:,.0f}"),
                f("Net of frictions", "net = gross - tax_drag - cost - slippage",
                  result=f"₪{a.net_trade_value_currency:,.0f}")])

    # 4) Behind your goal? Optimize across every lever to close the gap.
    recs += _behind_goal_recs(plan, snap, objective)
    recs += FeeAgent().recommendations(pdicts)  # Phase 3.2 fee optimizer

    # 5) Manage-the-holdings agents (Phase 4): per-holding Buy/Hold/Trim verdicts,
    #    sector hedging, momentum/trend and income/cost. Each is defensive - a
    #    data hiccup never breaks the Today view.
    trimmed = {(r.get("apply") or {}).get("ticker") for r in recs
               if (r.get("apply") or {}).get("kind") == "trim"}
    recs += _holding_verdict_recs(rows, snap, cap, trimmed)
    recs += _hedge_recs(rows, snap)
    recs += _momentum_recs(rows)
    recs += _income_cost_recs(pdicts, snap, objective)

    recs.sort(key=lambda r: _SEV.get(r["severity"], 9))
    # Phase 3.3: validate the Risk Agent's beta against history before surfacing.
    bt_holdings = [{"ticker": d["ticker"], "asset_class": d.get("asset_class") or "Equities",
                    "value_ils": d["quantity"] * d["current_price"]} for d in pdicts]
    bt = BacktestEngine().run(bt_holdings, portfolio_vol_pct=snap["avg_volatility_pct"])
    return {"count": len(recs), "objective": objective, "recommendations": recs[:12],
            "buy_ideas": _buy_ideas(snap),
            "risk_validation": {"beta_validated": bt.beta_validated,
                                "structural_beta": bt.structural_beta,
                                "risk_implied_beta": bt.risk_implied_beta,
                                "worst_event": bt.worst_event,
                                "worst_portfolio_drawdown_pct": bt.worst_portfolio_drawdown_pct,
                                "critique": bt.critique}}


# expected annual return by objective (rough asset-class blend; used to size the gap)
_OBJ_RETURN = {"Grow": 8.5, "Balanced": 6.5, "Preserve": 4.0, "Income": 5.0}


def _behind_goal_recs(plan, snap, objective) -> list[dict]:
    """When the plan won't reach the target on the current path, recommend the
    concrete levers to close the gap (each with a machine-applyable spec)."""
    out: list[dict] = []
    if plan is None or not getattr(plan, "target_amount", None):
        return out
    nav = snap["nav"]
    target = float(plan.target_amount)
    r = (_OBJ_RETURN.get(objective, 6.5)) / 100.0
    years = max(1, int(getattr(plan, "horizon_years", 10) or 10))
    # try to use the deadline year if present
    try:
        import datetime as _dt
        yr = int(str(getattr(plan, "target_date", "") or "")[:4])
        years = max(1, yr - _dt.date.today().year) or years
    except (ValueError, TypeError):
        pass
    projected = nav * (1 + r) ** years
    if projected >= target:
        return out  # on track on the current path

    gap = target - projected
    behind_pct = round((1 - projected / target) * 100) if target else 0
    sev = "CRITICAL" if behind_pct >= 40 else "HIGH"

    # Lever A — add money: required monthly contribution (future value of an annuity)
    rm = r / 12.0
    n = years * 12
    pmt = gap * rm / (((1 + rm) ** n) - 1) if rm else gap / n
    out.append({"id": _rid("behind", "contrib"), "dimension": "goal", "severity": sev,
                "title": "You're behind — add a monthly contribution",
                "action": f"On the current path you'd reach about {_ils(projected)} of your {_ils(target)} goal "
                          f"(~{behind_pct}% short). Investing about {_ils(pmt)}/month would close the gap by your deadline.",
                "how": [f"Set up a standing order of ~{_ils(pmt)}/month into this portfolio",
                        "Keep the same mix — regular contributions do the heavy lifting",
                        "Re-check here as your balance grows"],
                "est_amount": round(pmt, 2),
                "apply": {"kind": "none"}})
    out[-1]["audit_trail"] = audit_for("goal",
        raw_data={"nav": round(nav, 2), "target": round(target, 2), "projected": round(projected, 2),
                  "behind_pct": behind_pct, "annual_return_pct": round(r * 100, 2), "years": years},
        formulas=[
            f("Projection", "projected = NAV x (1+r)^years",
              substituted=f"{nav:,.0f} x (1+{r:.3f})^{years}", result=f"₪{projected:,.0f}"),
            f("Monthly contribution", "pmt = gap x (r/12) / ((1+r/12)^(12*years) - 1)",
              result=f"₪{pmt:,.0f}/mo")])

    # Lever B — shift to a higher-growth mix (only if not already Grow)
    if objective != "Grow":
        proj_grow = nav * (1 + _OBJ_RETURN["Grow"] / 100.0) ** years
        out.append({"id": _rid("behind", "grow"), "dimension": "goal", "severity": "HIGH",
                    "title": "Shift to a higher-growth mix",
                    "action": f"Switching from {objective} to a Grow mix raises expected growth, lifting the "
                              f"projection to about {_ils(proj_grow)} — but with bigger swings along the way.",
                    "how": ["Change your objective to Grow (more equities, fewer bonds)",
                            "Accepting this rebalances your holdings toward the Grow target",
                            "Make sure the extra volatility still fits your risk tolerance"],
                    "est_amount": round(proj_grow - projected, 2),
                    "apply": {"kind": "set_objective_and_rebalance", "objective": "Grow"}})

    # Lever C — extend the horizon
    import math
    need_years = math.log(target / nav) / math.log(1 + r) if nav > 0 and r > 0 else years + 5
    if need_years > years:
        new_years = int(math.ceil(need_years))
        out.append({"id": _rid("behind", "horizon"), "dimension": "goal", "severity": "MEDIUM",
                    "title": "Give it more time",
                    "action": f"At your current mix you'd need about {new_years} years (vs {years}) to reach "
                              f"{_ils(target)}. Extending the deadline makes the goal realistic without extra risk.",
                    "how": [f"Push your horizon out to ~{new_years} years",
                            "Accepting this updates your plan's horizon",
                            "Your projection and odds update immediately"],
                    "est_amount": None,
                    "apply": {"kind": "set_plan", "fields": {"horizon_years": new_years}}})

    # Lever D — set a realistic target
    out.append({"id": _rid("behind", "target"), "dimension": "goal", "severity": "LOW",
                "title": "Set a target you'll actually hit",
                "action": f"A realistic target on the current path is about {_ils(projected)} by your deadline.",
                "how": [f"Lower your target to ~{_ils(projected)}",
                        "Accepting this updates your plan's target amount",
                        "You can always raise it again later"],
                "est_amount": round(projected, 2),
                "apply": {"kind": "set_plan", "fields": {"target_amount": round(projected, 2)}}})
    return out


# ---------------------------------------------------------------------------
# Phase 4 - "manage my holdings" agents
# ---------------------------------------------------------------------------

def _fundamentals(ticker: str):
    """Best-effort fundamentals; None on any provider issue (never raises)."""
    try:
        from app.providers.registry import guarded_fundamentals
        return guarded_fundamentals(ticker)
    except Exception:
        return None


def _fund_score(f) -> float | None:
    """A compact 0-100 quality-of-fundamentals score for a single name."""
    if f is None:
        return None
    s, n = 0.0, 0
    if f.pe is not None:
        s += 100.0 if 0 < f.pe <= 15 else (60.0 if 0 < f.pe <= 30 else (20.0 if f.pe > 0 else 0.0)); n += 1
    if f.earnings_growth_pct is not None:
        s += max(0.0, min(100.0, 50.0 + f.earnings_growth_pct * 2.0)); n += 1
    if f.roe_pct is not None:
        s += max(0.0, min(100.0, f.roe_pct * 2.5)); n += 1
    if f.profit_margin_pct is not None:
        s += max(0.0, min(100.0, 50.0 + f.profit_margin_pct * 1.5)); n += 1
    if f.debt_to_equity is not None:
        s += max(0.0, min(100.0, 100.0 - f.debt_to_equity / 2.5)); n += 1
    return round(s / n, 1) if n else None


def _holding_verdict_recs(rows, snap, cap: float, trimmed: set) -> list[dict]:
    """A Buy-more / Hold / Trim verdict on each position you already own."""
    out: list[dict] = []
    nav = snap["nav"]
    if not nav:
        return out
    weights = snap["exposure_ticker"]
    for p in rows:
        tk = p.ticker
        f = _fundamentals(tk)
        score = _fund_score(f)
        if score is None:
            continue
        w = weights.get(tk, 0.0)
        price = float(p.current_price or 0)
        cost = float(p.cost_basis or 0)
        gain = price > cost
        if score >= 65 and w < cap * 0.6:
            verdict, sev = "Buy more", "LOW"
            action = (f"{tk} screens well (fundamentals {score:.0f}/100) and is only {w:.0%} of your book "
                      f"— adding on weakness is reasonable if it fits your plan.")
            how = [f"Consider topping up {tk} toward your target weight",
                   "Use limit orders and average in rather than buying all at once",
                   "Keep it under your concentration limit"]
        elif score < 40 and tk not in trimmed:
            verdict, sev = "Trim", "MEDIUM"
            action = (f"{tk} screens poorly on fundamentals ({score:.0f}/100)"
                      f"{' and you are sitting on a gain' if gain else ''} "
                      f"— consider trimming and redeploying into stronger names.")
            how = [f"Sell part of {tk} (start with ~25-50% of the position)",
                   "Redeploy into higher-scoring holdings or your target mix",
                   "Mind the tax on any realized gain"]
        else:
            verdict, sev = "Hold", "LOW"
            action = (f"{tk} looks fairly valued on fundamentals ({score:.0f}/100) at {w:.0%} of your book "
                      f"— no action needed; keep holding.")
            how = [f"Keep {tk} as-is",
                   "Re-check if the thesis or fundamentals change",
                   "Rebalance only if it drifts past your limit"]
        out.append({"id": _rid("verdict", tk), "dimension": "holding", "severity": sev,
                    "title": f"{verdict}: {tk}", "action": action, "how": how,
                    "est_amount": None, "apply": {"kind": "none"},
                    "meta": {"verdict": verdict, "fundamental_score": score,
                             "metrics": (f.model_dump() if f else None)}})
        out[-1]["audit_trail"] = audit_for("holding",
            raw_data={"ticker": tk, "weight": round(w, 4), "fundamental_score": score},
            formulas=[_fml("Fundamental score",
                        "score = mean(value, growth, quality, leverage)",
                        result=f"{score:.0f}/100")])
    return out


def _hedge_recs(rows, snap) -> list[dict]:
    """Flag sector/factor concentration and suggest a diversifier or hedge."""
    out: list[dict] = []
    nav = snap["nav"]
    if not nav or len(rows) < 2:
        return out
    sector_w: dict[str, float] = {}
    for p in rows:
        f = _fundamentals(p.ticker)
        sector = (f.sector if f and f.sector else "Unknown")
        val = float(p.quantity) * float(p.current_price or 0)
        sector_w[sector] = sector_w.get(sector, 0.0) + val / nav
    sector_w.pop("Unknown", None)
    if not sector_w:
        return out
    top_sector = max(sector_w, key=sector_w.get)
    w = sector_w[top_sector]
    if w >= 0.40:
        out.append({"id": _rid("hedge", top_sector), "dimension": "risk", "severity": "MEDIUM",
                    "title": f"Reduce {top_sector} concentration",
                    "action": (f"About {w:.0%} of your equity sits in {top_sector}. A shock to that one "
                               f"sector would hit you hard — diversify or add a hedge."),
                    "how": [f"Trim your most expensive {top_sector} names",
                            "Rotate the proceeds into under-represented sectors (e.g. an XLV/XLE/XLF sleeve)",
                            "Or add a low-correlation diversifier (bonds/BND, gold/GLD, or BTAL)"],
                    "est_amount": None, "apply": {"kind": "none"},
                    "meta": {"sector": top_sector, "weight": round(w, 4)}})
        out[-1]["audit_trail"] = audit_for("risk",
            raw_data={"sector": top_sector, "weight": round(w, 4)},
            formulas=[_fml("Sector weight", "w = sector_value / NAV", result=f"{w:.0%}")])
    return out


def _momentum_recs(rows) -> list[dict]:
    """Surface holdings in a strong down- or up-trend (price trend only, no fundamentals)."""
    out: list[dict] = []
    for p in rows:
        try:
            from app.providers.registry import guarded_history
            hist = guarded_history(p.ticker, days=200)
        except Exception:
            hist = None
        closes = [c for _d, c in hist] if hist else []
        if len(closes) < 60:
            continue
        long_ma = sum(closes[-150:]) / len(closes[-150:]) if len(closes) >= 150 else sum(closes) / len(closes)
        short_ma = sum(closes[-30:]) / 30.0
        last = closes[-1]
        ret = (last / closes[-min(120, len(closes))] - 1.0) * 100.0
        if short_ma < long_ma * 0.95 and ret < -10:
            out.append({"id": _rid("mom_dn", p.ticker), "dimension": "momentum", "severity": "MEDIUM",
                        "title": f"{p.ticker} is in a downtrend",
                        "action": (f"{p.ticker} is down ~{abs(ret):.0f}% and trading below its trend. "
                                   f"Re-check the thesis — cut it or add deliberately, don't drift."),
                        "how": [f"Review why you hold {p.ticker}",
                                "If the thesis is intact, this may be an entry; if not, trim",
                                "Set a price level that would change your mind"],
                        "est_amount": None, "apply": {"kind": "none"},
                        "meta": {"return_pct": round(ret, 1), "trend": "down"}})
            out[-1]["audit_trail"] = audit_for("momentum",
                raw_data={"ticker": p.ticker, "return_pct": round(ret, 1), "trend": "down"},
                formulas=[_fml("Trend", "ret = last / price_120d_ago - 1", result=f"{ret:.0f}%")])
        elif short_ma > long_ma * 1.05 and ret > 12:
            out.append({"id": _rid("mom_up", p.ticker), "dimension": "momentum", "severity": "LOW",
                        "title": f"{p.ticker} is in an uptrend",
                        "action": (f"{p.ticker} is up ~{ret:.0f}% and trending higher. Let winners run, "
                                   f"but watch that it doesn't grow past your concentration limit."),
                        "how": [f"Hold {p.ticker} while the trend holds",
                                "Trim only if it breaches your single-name cap",
                                "Consider a trailing stop to protect gains"],
                        "est_amount": None, "apply": {"kind": "none"},
                        "meta": {"return_pct": round(ret, 1), "trend": "up"}})
            out[-1]["audit_trail"] = audit_for("momentum",
                raw_data={"ticker": p.ticker, "return_pct": round(ret, 1), "trend": "up"},
                formulas=[_fml("Trend", "ret = last / price_120d_ago - 1", result=f"{ret:.0f}%")])
    return out[:3]


def _income_cost_recs(pdicts, snap, objective) -> list[dict]:
    """Cash drag and dividend-income opportunities (fees handled by the FeeAgent)."""
    out: list[dict] = []
    nav = snap["nav"]
    if not nav:
        return out
    cash_w = 0.0
    for d in pdicts:
        cls = (d.get("asset_class") or "").lower()
        if cls == "cash" or d["ticker"].upper() in {"BIL", "SHV", "SGOV", "CASH"}:
            cash_w += (d["quantity"] * d["current_price"]) / nav
    if cash_w >= 0.15:
        out.append({"id": _rid("cashdrag"), "dimension": "income", "severity": "MEDIUM",
                    "title": "Put idle cash to work",
                    "action": (f"About {cash_w:.0%} of your portfolio is in cash. Even a short-term bond or "
                               f"money-market ETF would earn yield on it instead of drifting."),
                    "how": ["Keep only your real emergency buffer in cash",
                            "Move the rest into a T-bill/money-market ETF (e.g. SGOV/BIL) or your target mix",
                            "Re-check after the next contribution"],
                    "est_amount": round(cash_w * nav, 2), "apply": {"kind": "none"},
                    "meta": {"cash_weight": round(cash_w, 4)}})
        out[-1]["audit_trail"] = audit_for("income",
            raw_data={"cash_weight": round(cash_w, 4), "nav": round(nav, 2)},
            formulas=[_fml("Cash weight", "cash_weight = cash_value / NAV", result=f"{cash_w:.0%}")])
    if (objective or "") == "Income":
        yields = []
        for d in pdicts:
            f = _fundamentals(d["ticker"])
            if f and f.dividend_yield_pct is not None:
                yields.append(f.dividend_yield_pct)
        if yields and (sum(yields) / len(yields)) < 2.0:
            avg = sum(yields) / len(yields)
            out.append({"id": _rid("income_yield"), "dimension": "income", "severity": "LOW",
                        "title": "Lift your portfolio yield",
                        "action": (f"Your objective is Income but your holdings average only {avg:.1f}% yield. "
                                   f"Tilting toward dividend payers would raise your cash income."),
                        "how": ["Add a dividend-focused sleeve (e.g. SCHD/VYM)",
                                "Favor profitable, low-payout-risk dividend names",
                                "Keep total-return in mind — don't chase the highest yield"],
                        "est_amount": None, "apply": {"kind": "none"},
                        "meta": {"avg_yield_pct": round(avg, 2)}})
            out[-1]["audit_trail"] = audit_for("income",
                raw_data={"avg_yield_pct": round(avg, 2)},
                formulas=[_fml("Average yield", "avg = mean(dividend_yield_pct)", result=f"{avg:.1f}%")])
    return out


def _buy_ideas(snap) -> list[dict]:
    """Top fundamentals-ranked buy ideas from the Opportunity Agent (informational)."""
    try:
        from app.agents.screener_agent import OpportunityAgent
        picks = OpportunityAgent().screen_equities(top_n=5)
        return [{"ticker": p.ticker, "name": p.name, "score": p.score,
                 "sector": p.sector, "reasons": p.reasons, "flags": p.flags,
                 "metrics": p.metrics} for p in picks]
    except Exception:
        return []


async def _rebalance_to(session, user, rows, objective: str) -> None:
    """Scale holdings so each asset class hits its objective target weight (NAV held constant)."""
    from collections import defaultdict
    plan = await get_plan(session, user)
    _sid = getattr(plan, "strategy", None)
    _s = _strat.get(_sid) if _sid else None
    target = (_s["target_allocation"] if _s else OBJ_TARGET.get(objective, OBJ_TARGET["Balanced"]))
    nav = sum(float(p.quantity) * float(p.current_price or 0) for p in rows)
    if not nav:
        return
    byc = defaultdict(list)
    for p in rows:
        byc[classify(p.ticker, p.market, (p.meta or {}).get("asset_class"))].append(p)
    present_weight = sum(w for c, w in target.items() if c in byc) or 1.0
    for c, rowsc in byc.items():
        w = target.get(c, 0.0)
        desired = (w / present_weight) * nav  # classes absent from target -> 0 (sold down)
        cur = sum(float(p.quantity) * float(p.current_price or 0) for p in rowsc)
        for p in rowsc:
            price = float(p.current_price or 0)
            if price <= 0:
                continue
            share = (float(p.quantity) * price / cur) if cur > 0 else 1.0 / len(rowsc)
            await update_position(session, user, str(p.id), quantity=round(desired * share / price, 4))


async def apply_recommendation(session: AsyncSession, user: User, rec_id: str) -> dict | None:
    """Accept = apply the recommendation to holdings/plan immediately."""
    built = await build_recommendations(session, user)
    rec = next((r for r in built.get("recommendations", []) if r["id"] == rec_id), None)
    if rec is None:
        return None
    spec = rec.get("apply") or {"kind": "none"}
    kind = spec.get("kind")
    rows = await list_positions(session, user)
    by_ticker = {p.ticker: p for p in rows}

    if kind == "trim":
        p = by_ticker.get(spec["ticker"])
        if p:
            await update_position(session, user, str(p.id),
                                  quantity=max(0.0, float(p.quantity) - float(spec["shares"])))
    elif kind == "sell_losers":
        for tk in spec.get("tickers", []):
            p = by_ticker.get(tk)
            if p:
                await delete_position(session, user, tk, p.market)
    elif kind == "rebalance_to_objective":
        plan = await get_plan(session, user)
        await _rebalance_to(session, user, rows, plan.objective if plan else "Balanced")
    elif kind == "set_objective_and_rebalance":
        await upsert_plan(session, user, objective=spec["objective"])
        await session.commit()
        rows = await list_positions(session, user)
        await _rebalance_to(session, user, rows, spec["objective"])
    elif kind == "set_plan":
        await upsert_plan(session, user, **spec.get("fields", {}))
        await session.commit()
    # kind == "none" -> acknowledged, nothing to mutate
    return {"applied": kind, "title": rec["title"]}
