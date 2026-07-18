"""Unified, actionable recommendations for the Today view (what to do + how)."""
from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.engines.allocation_engine import AllocationEngine
from app.models.tables import KVSetting, User
from app.services.allocation_mix import OBJ_TARGET, classify, current_mix
from app.services import strategies as _strat
from app.services.audit_trail import audit_for, f
from app.services.audit_trail import f as _fml  # alias: 'f' is shadowed by Fundamentals locals below
from app.agents.fee_agent import FeeAgent
from app.engines.backtest_engine import BacktestEngine
from app.services.intake_service import (
    credit_cash, delete_position, list_positions, update_position,
)
from app.services.plan_service import effective_caps, get_plan, upsert_plan
from app.services.portfolio_analytics import compute_snapshot, tax_opportunities

logger = logging.getLogger(__name__)

CLASS_ETF = {"Equities": "VTI", "Fixed Income": "BND", "Cash": "BIL",
             "Commodities": "DBC", "Real Estate": "VNQ", "Alternatives": "BTAL"}

_SEV = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

# Apply kinds that genuinely mutate holdings/plan/rules. Anything else is advice
# the app can't execute for you -- Accept on those used to fall through every
# branch, report "Done -- applied." and silently dismiss the card, so guidance
# looked like it had been carried out when nothing had happened.
_ACTIONABLE_KINDS = {"trim", "sell_losers", "fee_swap", "rebalance_to_objective",
                     "set_objective_and_rebalance", "set_plan", "create_rule", "create_rules"}


def _is_actionable(rec: dict) -> bool:
    return ((rec.get("apply") or {}).get("kind") or "none") in _ACTIONABLE_KINDS


def _rid(*parts) -> str:
    return "rec_" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:6]


def _ils(x) -> str:
    return f"₪{round(x):,}"


# Plain-language fallbacks so every card explains *why* it fired and its *impact*,
# even for the agent-built cards that don't set these fields explicitly. Kept
# qualitative on purpose — the advisor never invents numbers it hasn't computed.
_DIM_WHY = {
    "diversification": "Too much of your portfolio rides on a single position, region or currency.",
    "tax": "You're holding position(s) where a tax move could work in your favor.",
    "allocation": "Your current mix has drifted from your plan's target.",
    "goal": "On the current path your projection falls short of your goal.",
    "fees": "You're paying more in fund fees than a comparable cheaper fund would cost.",
    "macro": "The market backdrop has shifted, changing your near-term risk.",
    "liquidity": "A large share of your book may be hard to sell quickly.",
    "risk": "A risk metric is outside your comfort band.",
}
_DIM_IMPACT = {
    "diversification": "Spreads your risk so no single holding can dominate your outcome.",
    "tax": "Improves your after-tax return.",
    "allocation": "Realigns your mix with your plan.",
    "goal": "Moves you closer to reaching your goal on time.",
    "fees": "Keeps more of your return instead of paying it away in fees.",
    "macro": "Reduces your exposure to a near-term drawdown.",
    "liquidity": "Improves how quickly you could raise cash if you need to.",
    "risk": "Brings a risk measure back into your comfort band.",
}


def _ensure_why_impact(recs: list[dict]) -> list[dict]:
    """Guarantee every recommendation carries a 'why', an 'impact', and an
    'audit_trail' (the invariant every card must satisfy)."""
    for r in recs:
        dim = r.get("dimension", "")
        r.setdefault("apply", {"kind": "none"})
        r["actionable"] = _is_actionable(r)
        if not r.get("why"):
            r["why"] = _DIM_WHY.get(dim, "This helps your portfolio track your plan.")
        if not r.get("impact"):
            r["impact"] = _DIM_IMPACT.get(dim, "Moves your portfolio closer to your plan.")
        if not r.get("audit_trail"):
            rd: dict = {"reason": r.get("why") or r.get("action") or r.get("title") or dim,
                        "severity": r.get("severity")}
            if r.get("est_amount") is not None:
                rd["est_amount"] = r["est_amount"]
            r["audit_trail"] = audit_for(dim, raw_data=rd, formulas=[
                f("Basis", f"surfaced when the {dim or 'portfolio'} signal crosses its threshold",
                  substituted=(r.get("why") or "")[:160],
                  result=r.get("impact") or r.get("severity") or "flagged")])
    return recs


# --- Server-side dismissals (so the Today list and push notifications agree) ---
# An "Ignore" lasts up to this many days; if the recommendation is still relevant
# after that, it resurfaces (and can notify again).
_DISMISS_TTL_DAYS = 7
# "Done" is a stronger statement than "ignore", so it suppresses for longer -- but
# NOT forever. A permanent hide-list is exactly what caused the original
# notification/Today mismatch (push kept firing for cards the app had buried), so
# completed items still age out, and the UI shows the count with its own restore.
_DONE_TTL_DAYS = 90


def _dismiss_key(user: User) -> str:
    return f"dismissed_recs:{user.email}"


def _done_key(user: User) -> str:
    return f"completed_recs:{user.email}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc)  # unparseable -> treat as fresh


async def _load_map(session: AsyncSession, key: str) -> dict[str, str]:
    """Return {rec_id: marked_at_iso}. Tolerates the legacy list-of-ids shape."""
    row = await session.get(KVSetting, key)
    if not row:
        return {}
    try:
        data = json.loads(row.value)
    except Exception:  # noqa: BLE001
        return {}
    if isinstance(data, list):  # legacy: ids only -> give each a fresh window
        return {i: _now_iso() for i in data}
    return data if isinstance(data, dict) else {}


async def _active(session: AsyncSession, key: str, ttl_days: int) -> set[str]:
    m = await _load_map(session, key)
    if not m:
        return set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    return {rid for rid, ts in m.items() if _parse_dt(ts) >= cutoff}


async def _mark(session: AsyncSession, key: str, rec_id: str, ttl_days: int) -> None:
    m = await _load_map(session, key)
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    m = {k: v for k, v in m.items() if _parse_dt(v) >= cutoff}  # prune expired
    m[rec_id] = _now_iso()
    payload = json.dumps(m)
    row = await session.get(KVSetting, key)
    if row:
        row.value = payload
    else:
        session.add(KVSetting(key=key, value=payload))
    await session.commit()


async def _clear(session: AsyncSession, key: str, ttl_days: int) -> int:
    m = await _load_map(session, key)
    if not m:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    active = sum(1 for ts in m.values() if _parse_dt(ts) >= cutoff)
    row = await session.get(KVSetting, key)
    if row:
        row.value = json.dumps({})
        await session.commit()
    return active


async def load_dismissed(session: AsyncSession, user: User) -> set[str]:
    """Cards the user chose to IGNORE (7-day window, restorable)."""
    return await _active(session, _dismiss_key(user), _DISMISS_TTL_DAYS)


async def load_completed(session: AsyncSession, user: User) -> set[str]:
    """Cards the user marked as DONE (90-day window, tracked separately).

    Ignore and Done mean different things: "not now" vs "handled". Filing them in
    one bucket made "Mark as done" look identical to "Ignore" -- the card landed
    in the ignored list, which is what the user reported.
    """
    return await _active(session, _done_key(user), _DONE_TTL_DAYS)


async def dismiss_recommendation(session: AsyncSession, user: User, rec_id: str) -> None:
    await _mark(session, _dismiss_key(user), rec_id, _DISMISS_TTL_DAYS)


async def complete_recommendation(session: AsyncSession, user: User, rec_id: str) -> None:
    await _mark(session, _done_key(user), rec_id, _DONE_TTL_DAYS)


async def restore_dismissed(session: AsyncSession, user: User) -> int:
    """Bring back everything the user ignored (does not touch completed cards)."""
    return await _clear(session, _dismiss_key(user), _DISMISS_TTL_DAYS)


async def restore_completed(session: AsyncSession, user: User) -> int:
    """Bring back everything the user marked done."""
    return await _clear(session, _done_key(user), _DONE_TTL_DAYS)



def _reconcile(recs: list[dict], market: dict | None = None) -> list[dict]:
    """Collapse duplicate advice and drop cards that contradict each other.

    The Today list is assembled from ~10 independent agents that never consult
    one another, so a real portfolio produced, simultaneously: "Sell Cash" AND
    "Buy Equities" (two legs of one rebalance, both applying the identical
    action), "Put idle cash to work" (a third card for the same surplus), and
    "Markets look risk-off" telling the user to *raise* cash 5-10% while the
    other three told them to spend it. Acting on all four was impossible.

    Rules, in order:
      1. Merge multi-leg rebalance cards into one card for the whole move.
      2. Geographic and currency concentration collapse into one card when they
         describe the same exposure (a US-only book is also a USD-only book).
      3. Cash-drag is dropped when a rebalance already redeploys the cash.
      4. Risk-off vs deploy-cash: the allocation cards win (they are actionable
         and plan-derived); the macro card is reworded from "raise cash" to a
         timing note so the two no longer point in opposite directions.
    """
    market = market or {}
    by_id = {r.get("id"): r for r in recs}
    drop: set[str] = set()

    # 1) One rebalance, one card.
    rebal = [r for r in recs if (r.get("apply") or {}).get("kind") == "rebalance_to_objective"]
    if len(rebal) > 1:
        legs = [r["title"] for r in rebal]
        keep = rebal[0]
        keep["title"] = "Rebalance toward your target mix"
        keep["action"] = ("One rebalance covers the whole move: "
                          + "; ".join(f"{r['title']} (~{_ils(abs(r.get('est_amount') or 0))})"
                                      for r in rebal) + ".")
        keep["why"] = ("Your mix has drifted from your plan's target across more than one asset "
                       "class. These move together, so they're one decision, not several.")
        keep["meta"] = {**(keep.get("meta") or {}), "merged_legs": legs}
        for r in rebal[1:]:
            drop.add(r["id"])

    # 2) Region and currency concentration are usually the same fact twice.
    geo, cur = by_id.get(_rid("divrisk", "geo")), by_id.get(_rid("divrisk", "cur"))
    if geo and cur:
        geo["title"] = "Diversify beyond one region and currency"
        geo["action"] = ("Most of your money sits in a single region *and* the currency that goes "
                         "with it, so one country's downturn hits you twice. Spread new money "
                         "across other regions to fix both at once.")
        geo["meta"] = {**(geo.get("meta") or {}), "merged": ["geographic", "currency"]}
        drop.add(cur["id"])

    # 3) A rebalance that buys into the portfolio already spends the idle cash.
    redeploys_cash = any((r.get("apply") or {}).get("kind") == "rebalance_to_objective"
                         and r["id"] not in drop for r in recs)
    cash_drag = by_id.get(_rid("cashdrag"))
    if cash_drag and redeploys_cash:
        drop.add(cash_drag["id"])

    # 4) Don't tell someone to raise and spend cash in the same breath.
    macro = by_id.get(_rid("macro", "riskoff"))
    if macro and redeploys_cash and macro["id"] not in drop:
        macro["title"] = "Markets look risk-off — phase the rebalance in"
        macro["action"] = (f"The market backdrop is risk-off ({market.get('rationale', '')}). "
                           "That's not a reason to abandon the rebalance above, but it is a reason "
                           "to phase it in over a few weeks rather than all at once.")
        macro["why"] = ("A risk-off backdrop raises the odds of a near-term drawdown, so the timing "
                        "of a large purchase matters more than usual.")
        macro["impact"] = "Spreads entry risk without leaving you off-plan."
        macro["how"] = ["Split the rebalance into 2-4 tranches over a few weeks",
                        "Start with the broadest, least volatile leg",
                        "Keep your cash floor intact while you phase in"]

    return [r for r in recs if r.get("id") not in drop]



async def _war_room_recs(session: AsyncSession, user: User, rows) -> list[dict]:
    """Surface signals the agent pipeline actually approved as Today cards.

    The war room and the Today view were two disconnected pipelines: the agents
    could approve "Buy TEVA" and the Today list would never mention it, which is
    what the user reported. Both now read from the same decisions, so the war
    room is the audit trail *for* Today rather than a parallel universe.

    Only DISPLAYED (approved) decisions become cards, and only when the
    underlying signals are grounded in real price history.
    """
    out: list[dict] = []
    try:
        from app.api.routes.war_room import _war_room_payload
        payload = await _war_room_payload(session, user, rows)
    except Exception:  # noqa: BLE001
        logger.warning("war-room recommendations unavailable", exc_info=True)
        return out
    if not payload.get("grounded"):
        return out  # never turn sample prices into advice
    held = {(getattr(p, "ticker", "") or "").upper() for p in (rows or [])}
    for s in payload.get("sessions", []):
        if s.get("outcome") != "DISPLAYED":
            continue
        tk = (s.get("ticker") or "").upper()
        title = s.get("title") or f"Act on {tk}"
        conviction = next((ln.get("detail", {}) for ln in s.get("transcript", [])
                           if ln.get("agent") == "Decision"), {})
        impact_score = conviction.get("impact")
        confidence = conviction.get("confidence")
        alpha = next((ln.get("says") for ln in s.get("transcript", [])
                      if ln.get("agent") == "Alpha"), "")
        risk = next((ln.get("says") for ln in s.get("transcript", [])
                     if ln.get("agent") == "Risk"), "")
        verb = "Trim" if tk in held and s.get("outcome_label") == "Bulletproof" else "Review"
        out.append({
            "id": _rid("warroom", tk, s.get("outcome_label") or ""),
            "dimension": "signal",
            "severity": "MEDIUM" if (impact_score or 0) >= 50 else "LOW",
            "title": title,
            "why": (f"The agent pipeline approved this on the {s.get('outcome_label')} path. "
                    f"{alpha}").strip(),
            "action": (f"{verb} {tk}: the signal cleared risk, tax and adversary review"
                       + (f" at {confidence:.0f}% confidence" if confidence is not None else "")
                       + ". Open the war room for the full reasoning before acting."),
            "impact": (f"Impact score {impact_score:.0f}/100 from the scorer."
                       if impact_score is not None else "Scored by the decision engine."),
            "how": ["Open Agents -> war room and read the full transcript for this ticker",
                    "Check it against your plan's target mix before sizing",
                    "Signals are trend-divergence based, not a price forecast"],
            "est_amount": None,
            "apply": {"kind": "none"},
            "meta": {"source": "war_room", "ticker": tk, "impact": impact_score,
                     "confidence": confidence, "risk_note": risk},
        })
    return out[:3]


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
    # Which contributing agents failed this build. Each block below is defensive so a
    # data hiccup never breaks Today \u2014 but silence made a missing card
    # indistinguishable from "nothing to do", so failures are now logged and reported.
    degraded: list[str] = []

    # 1) Concentration trim
    if snap["max_weight"] > cap and nav:
        tk = max(snap["exposure_ticker"], key=snap["exposure_ticker"].get)
        w = snap["exposure_ticker"][tk]
        price = next((float(r.current_price or 0) for r in rows if r.ticker == tk), 0)
        trim = (w - cap) * nav
        shares = int(trim / price) if price else 0
        recs.append({"id": _rid("trim", tk), "dimension": "diversification", "severity": "HIGH",
                     "title": f"Trim {tk}",
                     "why": f"{tk} is {w:.0%} of your portfolio — above your {cap:.0%} single-holding limit, "
                            f"so a bad run in one name could sink the whole book.",
                     "action": f"Sell about {_ils(trim)} of {tk} (~{shares} shares) to bring it from "
                               f"{w:.0%} down to your {cap:.0%} limit.",
                     "impact": f"Cuts single-name risk: brings {tk} from {w:.0%} to your {cap:.0%} cap "
                               f"and frees ~{_ils(trim)} to spread across your plan's mix.",
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
                     "why": "You hold position(s) below what you paid — selling now realizes a loss you can "
                            "use to offset taxable gains before year-end.",
                     "action": f"Sell your losing position(s)"
                               f"{' (' + ', '.join(losers) + ')' if losers else ''} to realize the loss "
                               f"and save about {_ils(save)} in tax this year.",
                     "impact": f"Lowers this year's tax bill by about {_ils(save)}; you can re-buy after the "
                               f"wash-sale window if you still want the exposure.",
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
                     "why": f"Your {a.asset_class} weight ({mix.get(a.asset_class, 0):.0%}) has drifted from your "
                            f"{objective} target of {target.get(a.asset_class, 0):.0%}.",
                     "action": f"{a.action_type.title()} about {_ils(a.estimated_trade_value_currency)} of "
                               f"{a.asset_class} to move toward your {objective} target "
                               f"({target.get(a.asset_class, 0):.0%}).",
                     "impact": f"Moves ~{_ils(a.net_trade_value_currency)} (net of tax & costs) to realign your "
                               f"mix with your {objective} plan.",
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

    # 3b) Concentration in one region / currency, or a hard-to-sell book — the same
    #     vectors the "biggest risk" panel shows, surfaced here as actionable to-dos so
    #     a pushed risk alert always maps to something you can do in the app.
    try:
        from app.services.portfolio_analytics import risk_alerts as _risk_alerts
        for a in _risk_alerts(snap, cap).get("alerts", []):
            vec = a.get("vector")
            if vec == "geographic":
                recs.append({"id": _rid("divrisk", "geo"), "dimension": "diversification",
                             "severity": a.get("severity", "MEDIUM"), "title": "Diversify across regions",
                             "why": a.get("detail") or "Most of your money sits in a single region.",
                             "action": "Spread new money across more regions so one country's downturn can't "
                                       "sink the whole portfolio.",
                             "impact": "Lowers geographic concentration risk from over-reliance on one region.",
                             "how": ["Favor under-represented regions for your next contributions",
                                     "Consider a broad ex-home global ETF (e.g. VXUS) to balance exposure",
                                     "Re-check here as the mix evens out"],
                             "apply": {"kind": "none"}})
            elif vec == "currency":
                recs.append({"id": _rid("divrisk", "cur"), "dimension": "diversification",
                             "severity": a.get("severity", "MEDIUM"), "title": "Diversify your currency exposure",
                             "why": a.get("detail") or "Most of your money sits in a single currency.",
                             "action": "Add holdings priced in other currencies so an FX swing doesn't move your "
                                       "whole net worth at once.",
                             "impact": "Reduces the FX imbalance from having most of your money in one currency.",
                             "how": ["Add assets denominated in other major currencies",
                                     "A globally diversified fund spreads currency exposure automatically",
                                     "Re-check here as the balance improves"],
                             "apply": {"kind": "none"}})
            elif vec == "liquidity":
                recs.append({"id": _rid("divrisk", "liq"), "dimension": "liquidity",
                             "severity": a.get("severity", "HIGH"), "title": "Hold more liquid assets",
                             "why": a.get("detail") or "A large share of your book may be hard to sell quickly.",
                             "action": "Shift some of the least-tradable holdings into more liquid ones so you can "
                                       "raise cash without a fire-sale if you need to.",
                             "impact": "Improves how quickly you could exit without moving the price against you.",
                             "how": ["Identify your least-liquid holdings",
                                     "Trim a portion into broadly-traded ETFs or cash equivalents",
                                     "Keep enough liquidity for near-term needs"],
                             "apply": {"kind": "none"}})
    except Exception:  # noqa: BLE001
        logger.warning("risk-alert recommendations failed", exc_info=True)
        degraded.append("risk_alerts")

    # 4) Behind your goal? Optimize across every lever to close the gap.
    #    Reconcile the "current path" projection with the app's home panel — same
    #    Monte-Carlo engine — so the card, the digest and "Where this could end up"
    #    all cite one number instead of three.
    projected_median = None
    try:
        from app.api.routes.plan import _auto_target, _portfolio_stats
        from app.engines.simulation_engine import SimulationEngine
        _pstats = _portfolio_stats(rows)
        if _pstats.get("nav"):
            _pyears = max(1, int(getattr(plan, "horizon_years", 10) or 10)) if plan else 10
            _sim = SimulationEngine(seed=7).run(
                initial_value=_pstats["nav"], expected_return_pct=_pstats.get("expected_roi") or 6.0,
                volatility_pct=_pstats.get("volatility") or 12.0, horizon_years=_pyears,
                target_value=_auto_target(_pstats["nav"], plan))
            projected_median = float(_sim.nominal.p50)
    except Exception:  # noqa: BLE001
        projected_median = None
    recs += _behind_goal_recs(plan, snap, objective, projected_median)
    recs += FeeAgent().recommendations(pdicts)  # Phase 3.2 fee optimizer

    # 5) Manage-the-holdings agents (Phase 4): per-holding Buy/Hold/Trim verdicts,
    #    sector hedging, momentum/trend and income/cost. Each is defensive - a
    #    data hiccup never breaks the Today view.
    trimmed = {(r.get("apply") or {}).get("ticker") for r in recs
               if (r.get("apply") or {}).get("kind") == "trim"}
    recs += _holding_verdict_recs(rows, snap, cap, trimmed)
    recs += _hedge_recs(rows, snap)
    recs += _momentum_recs(rows, snap)
    recs += _income_cost_recs(pdicts, snap, objective)
    recs += _commodity_recs(rows, snap, objective)
    try:
        from app.services.performance_service import performance as _perf_fn
        recs += _benchmark_recs(await _perf_fn(session, user), objective)
    except Exception:  # noqa: BLE001 -- performance is best-effort, never break Today
        logger.warning("benchmark-lag recommendation failed", exc_info=True)
        degraded.append("performance")

    # Macro signal: factor the futures-derived market regime into the agents. A
    # risk-off backdrop surfaces a defensive action. Defensive - never break Today.
    market = {}
    try:
        from app.services.markets_service import cached_regime
        market = cached_regime()  # cache-only: never blocks Today on a network call
        if market.get("regime") == "risk-off":
            recs.append({"id": _rid("macro", "riskoff"), "dimension": "macro", "severity": "MEDIUM",
                         "title": "Markets look risk-off",
                         "why": f"The futures-derived market regime has turned risk-off ({market.get('rationale','')}).",
                         "action": (f"The market backdrop is risk-off ({market.get('rationale','')}). "
                                    "Consider keeping more cash on hand and trimming your most volatile "
                                    "positions until conditions calm."),
                         "impact": "More cash and less volatility reduce your exposure to a near-term drawdown.",
                         "how": ["Review your highest-volatility holdings",
                                 "Consider raising cash by ~5-10%",
                                 "Hold off on adding leverage or speculative names"]})
    except Exception:  # noqa: BLE001
        logger.warning("market-regime recommendation failed", exc_info=True)
        degraded.append("market_regime")
        market = {}

    # Signals the agent pipeline approved (same decisions the war room shows).
    try:
        recs += await _war_room_recs(session, user, rows)
    except Exception:  # noqa: BLE001
        logger.warning("war-room recommendations failed", exc_info=True)
        degraded.append("agent_signals")

    # Trading rules that have fired -> top-priority, user-defined actions.
    try:
        from app.services.rules_service import triggered_rule_recs
        recs += await triggered_rule_recs(session, user)
    except Exception:  # noqa: BLE001
        logger.warning("triggered trading-rule recommendations failed", exc_info=True)
        degraded.append("trading_rules")

    # Independent agents can contradict each other; reconcile before display.
    recs = _reconcile(recs, market)

    # Every card carries a plain-language "why" and "impact" (safety net for the
    # holding/hedge/momentum/fee agents that don't set them explicitly).
    _ensure_why_impact(recs)

    # Drop anything the user dismissed (server-side, so push + Today stay in sync).
    dismissed = await load_dismissed(session, user)
    completed = await load_completed(session, user)
    suppressed = done_count = 0
    if dismissed or completed:
        _n = len(recs)
        recs = [r for r in recs if r.get("id") not in dismissed]
        suppressed = _n - len(recs)
        _n = len(recs)
        recs = [r for r in recs if r.get("id") not in completed]
        done_count = _n - len(recs)

    recs.sort(key=lambda r: _SEV.get(r["severity"], 9))
    # Phase 3.3: validate the Risk Agent's beta against history before surfacing.
    bt_holdings = [{"ticker": d["ticker"], "asset_class": d.get("asset_class") or "Equities",
                    "value_ils": d["quantity"] * d["current_price"]} for d in pdicts]
    bt = BacktestEngine().run(bt_holdings, portfolio_vol_pct=snap["avg_volatility_pct"])
    return {"count": len(recs), "objective": objective, "recommendations": recs[:12],
            "market": market,
            # Honesty signals for the Today empty state: "nothing to do",
            # "you ignored everything" and "an agent failed" are three different things.
            "dismissed_count": suppressed,
            "completed_count": done_count,
            "degraded": degraded,
            "buy_ideas": _buy_ideas(snap),
            "risk_validation": {"beta_validated": bt.beta_validated,
                                "structural_beta": bt.structural_beta,
                                "risk_implied_beta": bt.risk_implied_beta,
                                "worst_event": bt.worst_event,
                                "worst_portfolio_drawdown_pct": bt.worst_portfolio_drawdown_pct,
                                "critique": bt.critique}}


# expected annual return by objective (rough asset-class blend; used to size the gap)
_OBJ_RETURN = {"Grow": 8.5, "Balanced": 6.5, "Preserve": 4.0, "Income": 5.0}


def _behind_goal_recs(plan, snap, objective, projected_override=None) -> list[dict]:
    """When the plan won't reach the target on the current path, recommend the
    concrete levers to close the gap (each with a machine-applyable spec).

    projected_override: when provided (the Monte-Carlo median from the app's home
    panel), it is used as the "current path" projection so the card, the digest and
    "Where this could end up" all agree on one number instead of diverging."""
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
    deterministic = nav * (1 + r) ** years
    if projected_override and projected_override > 0:
        projected, proj_basis = float(projected_override), "montecarlo"
    else:
        projected, proj_basis = deterministic, "fixed-return"
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
                "why": f"On the current path you'd reach about {_ils(projected)} of your {_ils(target)} goal "
                       f"(~{behind_pct}% short), so nothing changes unless you add to it.",
                "action": f"On the current path you'd reach about {_ils(projected)} of your {_ils(target)} goal "
                          f"(~{behind_pct}% short). Investing about {_ils(pmt)}/month would close the gap by your deadline.",
                "impact": f"About {_ils(pmt)}/month closes the ~{_ils(gap)} gap and puts your {_ils(target)} goal "
                          f"back within reach by your deadline.",
                "how": [f"Set up a standing order of ~{_ils(pmt)}/month into this portfolio",
                        "Keep the same mix — regular contributions do the heavy lifting",
                        "Re-check here as your balance grows"],
                "est_amount": round(pmt, 2),
                "apply": {"kind": "none"}})
    _proj_formula = (f("Projection", "projected = Monte-Carlo median (10,000 sims)",
                       substituted=f"NAV {nav:,.0f}, {years}y", result=f"₪{projected:,.0f}")
                     if proj_basis == "montecarlo" else
                     f("Projection", "projected = NAV x (1+r)^years",
                       substituted=f"{nav:,.0f} x (1+{r:.3f})^{years}", result=f"₪{projected:,.0f}"))
    out[-1]["audit_trail"] = audit_for("goal",
        raw_data={"nav": round(nav, 2), "target": round(target, 2), "projected": round(projected, 2),
                  "projection_basis": proj_basis, "behind_pct": behind_pct,
                  "annual_return_pct": round(r * 100, 2), "years": years},
        formulas=[
            _proj_formula,
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


def _daily_vol_pct(closes: list[float], window: int = 21) -> float | None:
    """Std-dev of daily returns over the last `window` sessions, in percent.

    Returns None when there isn't enough clean history to be meaningful — callers
    fall back to a conservative fixed buffer rather than inventing a number.
    """
    tail = closes[-(window + 1):]
    rets = [tail[i] / tail[i - 1] - 1.0 for i in range(1, len(tail)) if tail[i - 1]]
    if len(rets) < 5:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return (var ** 0.5) * 100.0


def stop_buffer_pct(closes: list[float], *, horizon_days: int = 10, k: float = 1.5,
                    lo: float = 8.0, hi: float = 20.0) -> float:
    """A volatility-scaled buffer = k std-devs of a `horizon`-day move, clamped to
    a sane band. Grounded in the holding's own realized volatility (not a guess);
    falls back to the low bound when volatility can't be computed."""
    v = _daily_vol_pct(closes)
    if v is None:
        return lo
    raw = k * v * math.sqrt(horizon_days)
    return float(min(hi, max(lo, round(raw))))


def _momentum_recs(rows, snap) -> list[dict]:
    """Surface holdings in a strong down- or up-trend and turn each into a
    concrete, one-click discipline rule (stop-loss / trailing stop). Accept arms
    the rule — the app never trades; a hit raises an alert + a Today to-do."""
    out: list[dict] = []
    weights = (snap or {}).get("exposure_ticker") or {}
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
        weight_pct = round((weights.get(p.ticker, 0) or 0) * 100, 1)
        if short_ma < long_ma * 0.95 and ret < -10:
            buf = stop_buffer_pct(closes, k=1.5, lo=8.0, hi=15.0)
            stop = round(last * (1 - buf / 100.0), 2)
            rule = {"ticker": p.ticker, "rule_type": "stop_loss", "mode": "price", "level": stop,
                    "note": f"From downtrend alert (~{buf:.0f}% below {last:.2f})"}
            out.append({"id": _rid("mom_dn", p.ticker), "dimension": "momentum", "severity": "MEDIUM",
                        "title": f"{p.ticker} is in a downtrend",
                        "action": (f"{p.ticker} is down ~{abs(ret):.0f}% and trading below its trend. "
                                   f"Cap further downside with a concrete stop instead of drifting."),
                        "why": (f"{p.ticker} has broken below its longer-term trend, so the odds of further "
                                f"downside are higher — a pre-set stop makes the exit decision now, not in a panic."),
                        "impact": (f"Accept arms a stop-loss on {p.ticker} at {stop:.2f} (~{buf:.0f}% below today's "
                                   f"{last:.2f}). If it falls to there you get an alert + a Today to-do to sell."),
                        "how": [f"Accept → arms a stop-loss at {stop:.2f} (~{buf:.0f}% below today)",
                                f"You get an alert + a Today to-do if {p.ticker} reaches it",
                                "Adjust or remove it any time under Trading rules"],
                        "est_amount": None,
                        "apply": {"kind": "create_rules", "rules": [rule]},
                        "meta": {"return_pct": round(ret, 1), "trend": "down", "stop": stop, "buffer_pct": buf}})
            out[-1]["audit_trail"] = audit_for("momentum",
                raw_data={"ticker": p.ticker, "return_pct": round(ret, 1), "trend": "down",
                          "last": round(last, 2), "buffer_pct": buf},
                formulas=[_fml("Trend", "ret = last / price_120d_ago - 1", result=f"{ret:.0f}%"),
                          _fml("Buffer", "buffer = clamp(1.5 x daily_vol x sqrt(10), 8%, 15%)", result=f"{buf:.0f}%"),
                          _fml("Stop level", "stop = last x (1 - buffer)", result=f"{stop:.2f}")])
        elif short_ma > long_ma * 1.05 and ret > 12:
            buf = stop_buffer_pct(closes, k=2.0, lo=10.0, hi=20.0)
            rules = [{"ticker": p.ticker, "rule_type": "trailing_stop", "mode": "pct", "level": buf,
                      "note": f"From uptrend alert (protect gains, {buf:.0f}% trail)"}]
            cap_line = ""
            if weight_pct >= 12:
                cap = float(max(15.0, 5 * math.ceil(weight_pct / 5)))
                rules.append({"ticker": p.ticker, "rule_type": "max_weight", "mode": "pct", "level": cap,
                              "note": f"Keep {p.ticker} under {cap:.0f}% of the book"})
                cap_line = f" It's already {weight_pct:.0f}% of your book, so this also caps it at {cap:.0f}%."
            how = [f"Accept → arms a {buf:.0f}% trailing stop (protects gains, lets it run)"]
            if len(rules) > 1:
                how.append(f"Also caps {p.ticker} at {rules[-1]['level']:.0f}% of your portfolio")
            how.append("You get an alert + a Today to-do if either triggers")
            out.append({"id": _rid("mom_up", p.ticker), "dimension": "momentum", "severity": "LOW",
                        "title": f"{p.ticker} is in an uptrend",
                        "action": (f"{p.ticker} is up ~{ret:.0f}% and trending higher. Let the winner run, "
                                   f"but lock in a floor so a reversal doesn't give it all back."),
                        "why": (f"Trends tend to persist, so the play is to stay in {p.ticker} while protecting "
                                f"the gain — not to sell early on strength."),
                        "impact": (f"Accept arms a {buf:.0f}% trailing stop on {p.ticker}: it keeps running with the "
                                   f"trend, but if it drops {buf:.0f}% from its peak you get an alert to take "
                                   f"profit.{cap_line}"),
                        "how": how,
                        "est_amount": None,
                        "apply": {"kind": "create_rules", "rules": rules},
                        "meta": {"return_pct": round(ret, 1), "trend": "up", "trail_pct": buf,
                                 "weight_pct": weight_pct}})
            out[-1]["audit_trail"] = audit_for("momentum",
                raw_data={"ticker": p.ticker, "return_pct": round(ret, 1), "trend": "up", "weight_pct": weight_pct},
                formulas=[_fml("Trend", "ret = last / price_120d_ago - 1", result=f"{ret:.0f}%"),
                          _fml("Trailing stop", "trail = clamp(2 x daily_vol x sqrt(10), 10%, 20%)", result=f"{buf:.0f}%")])
    return out[:3]


def _income_cost_recs(pdicts, snap, objective) -> list[dict]:
    """Cash drag and dividend-income opportunities (fees handled by the FeeAgent)."""
    out: list[dict] = []
    nav = snap["nav"]
    if not nav:
        return out
    # NAV is FX-normalized, so the numerator must be too -- a USD money-market
    # sleeve was otherwise counted at ~a third of its real weight.
    from app.services.fx import fx_rate as _fx, price_currency as _pc
    cash_w = 0.0
    for d in pdicts:
        cls = (d.get("asset_class") or "").lower()
        if cls == "cash" or d["ticker"].upper() in {"BIL", "SHV", "SGOV", "CASH"}:
            _r = _fx(_pc(d.get("market"), None))
            cash_w += (d["quantity"] * d["current_price"] * _r) / nav
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


def _benchmark_recs(perf, objective) -> list[dict]:
    """Turn the performance-vs-benchmark read into an improvement action when the
    portfolio is trailing its benchmark by a meaningful margin (grounded in the
    real excess-return number, never invented)."""
    out: list[dict] = []
    if not isinstance(perf, dict) or not perf.get("ok"):
        return out
    ex = perf.get("excess_return_pct")
    if ex is None or ex > -3.0:
        return out
    bench = perf.get("benchmark", "the benchmark")
    bret, pret = perf.get("benchmark_return_pct"), perf.get("total_return_pct")
    sev = "HIGH" if ex <= -10.0 else "MEDIUM"
    detail = f" ({pret:.1f}% vs {bench} {bret:.1f}%)" if (bret is not None and pret is not None) else ""
    out.append({"id": _rid("benchmark_lag", bench), "dimension": "performance", "severity": sev,
                "title": f"You're trailing {bench}",
                "action": (f"Over this window your portfolio is about {abs(ex):.1f}% behind {bench}{detail}. "
                           f"Worth checking what's dragging before the gap compounds."),
                "why": (f"Sustained underperformance vs {bench} usually traces to one of three things: a few big "
                        f"laggards, paying too much in fund fees, or a mix that has drifted off target."),
                "impact": f"Closing a {abs(ex):.1f}% annual gap compounds into a large sum over your horizon.",
                "how": ["Check your biggest laggards in Holdings — trim or re-confirm the thesis",
                        "Review fund fees — cheaper index exposure tracks the benchmark more closely",
                        "Rebalance toward your target mix if you have drifted"],
                "est_amount": None, "apply": {"kind": "none"},
                "meta": {"excess_pct": round(ex, 1), "benchmark": bench}})
    out[-1]["audit_trail"] = audit_for("performance",
        raw_data={"excess_pct": round(ex, 1), "benchmark": bench,
                  "portfolio_return_pct": pret, "benchmark_return_pct": bret},
        formulas=[_fml("Excess return", "excess = portfolio_return - benchmark_return", result=f"{ex:.1f}%")])
    return out


def _commodity_recs(rows, snap, objective) -> list[dict]:
    """Flag a missing commodities sleeve and name concrete, screener-ranked picks."""
    out: list[dict] = []
    target = (OBJ_TARGET.get(objective or "Balanced", {}) or {}).get("Commodities", 0.0)
    if target <= 0:
        return out
    mix, nav = current_mix(rows)
    if not nav:
        return out
    com_w = mix.get("Commodities", 0.0)
    if com_w >= max(0.03, target * 0.5):
        return out  # already holds a meaningful sleeve
    picks = []
    try:
        from app.agents.screener_agent import OpportunityAgent
        picks = OpportunityAgent().screen_commodities(top_n=3)
    except Exception:  # noqa: BLE001
        picks = []
    names = [(f"{p.ticker} ({p.name})" if getattr(p, "name", "") else p.ticker) for p in picks][:3]
    pick_line = ", ".join(names) if names else "a broad basket (DBC), gold (GLD) or agriculture (DBA)"
    out.append({"id": _rid("commodity_add"), "dimension": "diversification", "severity": "LOW",
                "title": "Add a commodities sleeve",
                "action": (f"You hold about {com_w:.0%} in commodities but your {objective or 'Balanced'} target is "
                           f"~{target:.0%}. A small sleeve diversifies away from stocks and bonds."),
                "why": (f"Commodities tend to move differently from equities and bonds, so a small allocation "
                        f"cushions the portfolio when stocks and bonds fall together — your target holds ~{target:.0%}."),
                "impact": "Adds a low-correlation diversifier, smoothing your overall swings.",
                "how": [f"Consider {pick_line}",
                        f"Size it toward your ~{target:.0%} target — a little goes a long way",
                        "Add it under Holdings → Add commodities & real assets"],
                "est_amount": None, "apply": {"kind": "none"},
                "meta": {"commodity_weight": round(com_w, 4), "target": target,
                         "picks": [p.ticker for p in picks]}})
    out[-1]["audit_trail"] = audit_for("diversification",
        raw_data={"commodity_weight": round(com_w, 4), "target": target, "picks": [p.ticker for p in picks]},
        formulas=[_fml("Commodity gap", "gap = target - current", result=f"{(target - com_w):.0%}")])
    return out


def _buy_ideas(snap) -> list[dict]:
    """Top fundamentals-ranked equity buy ideas plus screener-ranked commodity
    picks from the Opportunity Agent (informational)."""
    try:
        from app.agents.screener_agent import OpportunityAgent
        ag = OpportunityAgent()
        out = [{"ticker": p.ticker, "name": p.name, "score": p.score, "kind": p.kind,
                "sector": p.sector, "reasons": p.reasons, "flags": p.flags,
                "metrics": p.metrics} for p in ag.screen_equities(top_n=5)]
        try:
            out += [{"ticker": p.ticker, "name": p.name, "score": p.score, "kind": p.kind,
                     "sector": "Commodities", "reasons": p.reasons, "flags": p.flags,
                     "metrics": p.metrics} for p in ag.screen_commodities(top_n=3)]
        except Exception:  # noqa: BLE001
            pass
        return out
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


def _sale_value_ils(qty: float, price: float, basis: float, market, meta) -> tuple[float, float]:
    """Value a full sale of `qty` shares in ILS: (gross_ils, estimated_tax_ils).

    Tax is Israeli CGT on the realized *gain* only (losses owe nothing — they are
    what a harvest realizes), so proceeds land net of tax as spendable liquidity.
    """
    from app.core.config import get_settings
    from app.services.fx import fx_rate, price_currency
    rate = fx_rate(price_currency(market, meta))
    gross = qty * price * rate
    gain = (price - basis) * qty * rate
    tax = max(0.0, gain) * float(get_settings().cgt_rate)
    return gross, tax


async def apply_recommendation(session: AsyncSession, user: User, rec_id: str) -> dict | None:
    """Accept = apply the recommendation to holdings/plan immediately.

    Sales don't just delete holdings — the net-of-tax proceeds are credited to a
    visible CASH position, so accepting a 'sell' shows up as liquidity you can see
    and redeploy (rather than value silently vanishing).
    """
    built = await build_recommendations(session, user)
    rec = next((r for r in built.get("recommendations", []) if r["id"] == rec_id), None)
    if rec is None:
        return None
    spec = rec.get("apply") or {"kind": "none"}
    kind = spec.get("kind")
    rows = await list_positions(session, user)
    by_ticker = {p.ticker: p for p in rows}
    detail: dict = {}

    if kind == "trim":
        p = by_ticker.get(spec["ticker"])
        if p:
            shares = min(float(spec["shares"]), float(p.quantity))
            gross, tax = _sale_value_ils(shares, float(p.current_price or 0),
                                         float(p.cost_basis or 0), p.market, p.meta)
            await update_position(session, user, str(p.id),
                                  quantity=max(0.0, float(p.quantity) - shares))
            credited = await credit_cash(session, user, gross - tax)
            detail = {"sold": [spec["ticker"]], "cash_added_ils": round(credited, 2),
                      "tax_ils": round(tax, 2)}
    elif kind == "sell_losers":
        sold, proceeds, tax_total = [], 0.0, 0.0
        for tk in spec.get("tickers", []):
            p = by_ticker.get(tk)
            if p:
                gross, tax = _sale_value_ils(float(p.quantity), float(p.current_price or 0),
                                             float(p.cost_basis or 0), p.market, p.meta)
                await delete_position(session, user, tk, p.market)
                proceeds += (gross - tax)
                tax_total += tax
                sold.append(tk)
        credited = await credit_cash(session, user, proceeds)
        detail = {"sold": sold, "cash_added_ils": round(credited, 2), "tax_ils": round(tax_total, 2)}
    elif kind == "fee_swap":
        detail = await _apply_fee_swap(session, user, by_ticker, spec)
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
    elif kind in ("create_rule", "create_rules"):
        specs = spec.get("rules") if kind == "create_rules" else [spec]
        created = []
        for rspec in (specs or []):
            made = await _create_rule_from_spec(session, user, rspec)
            if made:
                created.append(made)
        detail = {"rules_created": created}
    # kind == "none" -> acknowledged only; say so rather than claiming an edit
    if kind not in _ACTIONABLE_KINDS:
        return {"applied": "none", "advisory": True, "title": rec["title"],
                "note": "Marked as done. This one is guidance -- nothing was bought or sold."}
    return {"applied": kind, "title": rec["title"], **detail}


async def _create_rule_from_spec(session: AsyncSession, user: User, spec: dict) -> dict | None:
    """Arm a trading rule from an Accept spec. Returns a compact summary for the
    'what changed' confirmation, or None if the spec was malformed."""
    from app.services.rules_service import create_rule as _mk
    try:
        rule = await _mk(session, user, ticker=spec["ticker"], rule_type=spec["rule_type"],
                         mode=spec.get("mode", "pct"), level=float(spec["level"]),
                         note=spec.get("note"))
    except (KeyError, ValueError, TypeError):
        return None
    unit = "₪" if rule.mode == "price" else "%"
    pretty = rule.rule_type.replace("_", " ")
    return {"ticker": rule.ticker, "rule_type": rule.rule_type, "mode": rule.mode,
            "level": rule.level, "label": f"{rule.ticker} {pretty} {rule.level:g}{unit}"}


async def _apply_fee_swap(session: AsyncSession, user: User, by_ticker: dict, spec: dict) -> dict:
    """Sell the high-fee fund and buy the cheaper equivalent for the same value.

    The replacement is priced live so the 30-min reprice job keeps it correct; if
    it can't be priced, we fall back to leaving the net proceeds as cash rather
    than creating a mis-priced holding.
    """
    from app.providers.registry import guarded_quote
    from app.schemas.intake import IntakePosition
    from app.schemas.state_machine import Market
    from app.services.fx import fx_rate
    from app.services.intake_service import ensure_account, ensure_entity, upsert_positions

    sell_tk, buy_tk = spec.get("sell"), spec.get("buy")
    p = by_ticker.get(sell_tk)
    if not p or not buy_tk:
        return {"swapped": False}
    gross, tax = _sale_value_ils(float(p.quantity), float(p.current_price or 0),
                                 float(p.cost_basis or 0), p.market, p.meta)
    net_ils = max(0.0, gross - tax)
    await delete_position(session, user, sell_tk, p.market)
    try:
        q = guarded_quote(buy_tk)
        price, ccy = float(q.price), (getattr(q, "currency", None) or "USD")
    except Exception:  # noqa: BLE001 — no live price -> keep proceeds as cash, don't misprice
        credited = await credit_cash(session, user, net_ils)
        return {"swapped": False, "sold": [sell_tk], "cash_added_ils": round(credited, 2),
                "tax_ils": round(tax, 2), "note": f"Sold {sell_tk}; couldn't price {buy_tk} — held as cash."}
    if price <= 0:
        credited = await credit_cash(session, user, net_ils)
        return {"swapped": False, "sold": [sell_tk], "cash_added_ils": round(credited, 2),
                "tax_ils": round(tax, 2), "note": f"Sold {sell_tk}; no live price for {buy_tk} — held as cash."}
    native = net_ils / (fx_rate(ccy) or 1.0)  # ILS proceeds -> the buy's trading currency
    qty = native / price
    mk = p.market if p.market in {m.value for m in Market} else "NYSE"
    ip = IntakePosition(ticker=buy_tk.upper(), market=Market(mk), depth=1,
                        spot_price=price, listing_price=price, quantity=qty, cost_basis=price,
                        asset_class=spec.get("asset_class") or "Equities",
                        expense_ratio_pct=spec.get("buy_expense_ratio_pct"))
    entity = await ensure_entity(session, user, "Personal", "Personal")
    account = await ensure_account(session, entity, "Main")
    await upsert_positions(session, account, [ip])
    await session.commit()
    return {"swapped": True, "sold": [sell_tk], "bought": buy_tk.upper(),
            "value_ils": round(net_ils, 2), "tax_ils": round(tax, 2)}
