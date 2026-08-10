"""Unified, actionable recommendations for the Today view (what to do + how)."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
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

# How far the sleeve may wander from the size you chose before Today says so.
# Wide enough that ordinary price movement does not nag; narrow enough that a
# sleeve quietly becoming something else gets noticed.
SLEEVE_DRIFT_BAND_PCT = 5.0

# Apply kinds that genuinely mutate holdings/plan/rules. Anything else is advice
# the app can't execute for you -- Accept on those used to fall through every
# branch, report "Done -- applied." and silently dismiss the card, so guidance
# looked like it had been carried out when nothing had happened.
_ACTIONABLE_KINDS = {"trim", "sell_losers", "fee_swap", "rebalance_to_objective",
                     "set_objective_and_rebalance", "set_plan", "create_rule", "create_rules",
                     "buy_funded", "sell_position", "redeploy_cash", "fund_sleeve"}


def _agent_tx(session: AsyncSession):
    """A SAVEPOINT around one agent, so its failure cannot kill the request.

    Every "defensive" handler here catches an agent's failure, logs it, marks
    the pipeline degraded and carries on with the SAME session. SQLite tolerates
    that -- which is why the entire local suite was green -- but Postgres does
    not: the first failed statement ABORTS the transaction, and every query
    after it raises InFailedSQLTransactionError. Observed in production as a 500
    from /recommendations, with load_dismissed dying on "current transaction is
    aborted" long after the agent that actually broke.

    The obvious repair, session.rollback(), is worse: rollback EXPIRES every
    loaded ORM object, so the positions this function keeps using would each
    trigger a lazy reload and raise MissingGreenlet instead. A savepoint undoes
    only the failed agent's own statements and leaves both the outer transaction
    and the identity map intact.
    """
    return session.begin_nested()


def _is_actionable(rec: dict) -> bool:
    return ((rec.get("apply") or {}).get("kind") or "none") in _ACTIONABLE_KINDS


class _Stopwatch:
    """Per-agent timings for /recommendations.

    The endpoint took 24.2s, then ~5s after the provider cache fix, and there
    was no way to see WHICH of the ~10 agents was responsible -- so the next
    step would have been another guess. Timings ship in the response so the cost
    is attributable instead of inferred.
    """

    def __init__(self) -> None:
        self.marks: dict[str, int] = {}
        self._last = time.perf_counter()

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        self.marks[name] = int((now - self._last) * 1000)
        self._last = now

    def slowest(self, n: int = 3) -> list:
        return sorted(self.marks.items(), key=lambda kv: kv[1], reverse=True)[:n]


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


async def _resolve_if_rule_card(session: AsyncSession, user: User, rec_id: str,
                                outcome: str) -> None:
    """Rule cards carry their rule's id in the card id (``rule_<8 hex>``).

    A 7-day dismissal hides the *card*, but the rule itself stayed latched
    ``triggered`` forever — so the red "N trading rules triggered" banner, which
    reads the rules directly, kept counting work the user had already dealt with.
    """
    if not rec_id.startswith("rule_"):
        return
    from app.services.rules_service import resolve_rule
    try:
        await resolve_rule(session, user, rec_id[5:], outcome)
    except Exception:  # noqa: BLE001 -- hiding the card must never fail on this
        logger.warning("could not resolve rule for %s", rec_id, exc_info=False)


async def dismiss_recommendation(session: AsyncSession, user: User, rec_id: str) -> None:
    await _mark(session, _dismiss_key(user), rec_id, _DISMISS_TTL_DAYS)
    await _resolve_if_rule_card(session, user, rec_id, "dismissed")


async def complete_recommendation(session: AsyncSession, user: User, rec_id: str) -> None:
    await _mark(session, _done_key(user), rec_id, _DONE_TTL_DAYS)
    await _resolve_if_rule_card(session, user, rec_id, "acknowledged")


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
    """Turn approved agent signals into sized, plan-checked, executable cards.

    Two things were missing before. The war room and Today ran as separate
    pipelines, so the agents could approve a signal Today never mentioned. And
    the resulting card said "review this" with no size and no funding, which is
    advice, not an action.

    Now every promoted signal is checked against the plan first -- asset-class
    target, single-name concentration cap, available funding -- and is either
    sized concretely or dropped with the reason. A signal the plan can't
    accommodate is not a recommendation.
    """
    out: list[dict] = []
    try:
        from app.api.routes.war_room import _war_room_payload
        # narrate=False: the Gemini prose is a war-room-view flourish and is
        # never rendered on a Today card, but it cost 5.4-6.7s of the ~5s
        # endpoint -- one live LLM call per signal, on the event loop, on a
        # single-worker server. Measured: war_room 6726ms vs every other agent
        # under 50ms.
        payload = await _war_room_payload(session, user, rows, narrate=False)
    except Exception:  # noqa: BLE001
        logger.warning("war-room recommendations unavailable", exc_info=True)
        return out
    if not payload.get("grounded"):
        return out  # never turn sample prices into advice

    from app.services import funding_service as _fund
    from app.services.intake_service import get_cash

    plan = await get_plan(session, user)
    objective = plan.objective if plan else "Balanced"
    cap = effective_caps(plan)["concentration_cap"]
    pdicts = [{"ticker": p.ticker, "market": p.market, "quantity": float(p.quantity),
               "cost_basis": float(p.cost_basis), "current_price": float(p.current_price or 0),
               "asset_class": (p.meta or {}).get("asset_class")} for p in (rows or [])]
    snap = compute_snapshot(pdicts) if pdicts else {"nav": 0.0}
    nav = snap.get("nav") or 0.0
    mix, _ = current_mix(rows or [])
    cash = await get_cash(session, user)
    held = {(getattr(p, "ticker", "") or "").upper(): p for p in (rows or [])}
    target = OBJ_TARGET.get(objective, OBJ_TARGET["Balanced"])

    for s in payload.get("sessions", []):
        if s.get("outcome") != "DISPLAYED" or len(out) >= 3:
            continue
        tk = (s.get("ticker") or "").upper()
        det = next((ln.get("detail", {}) for ln in s.get("transcript", [])
                    if ln.get("agent") == "Decision"), {})
        impact, confidence = det.get("impact"), det.get("confidence")
        alpha = next((ln.get("says") for ln in s.get("transcript", [])
                      if ln.get("agent") == "Alpha"), "")
        pos = held.get(tk)
        cls = classify(tk, getattr(pos, "market", "NYSE") if pos else "NYSE",
                       ((pos.meta or {}).get("asset_class") if pos is not None else None))
        weight = snap.get("exposure_ticker", {}).get(tk, 0.0)

        if s.get("action_type") == "REBALANCE" or (pos is not None and weight > cap):
            # Already held and over the cap -> a concrete trim back to the cap.
            if pos is None or not nav:
                continue
            price_ils = _sale_value_ils(1.0, float(pos.current_price or 0),
                                        float(pos.cost_basis or 0), pos.market, pos.meta)[0]
            excess_ils = max(0.0, (weight - cap) * nav)
            shares = int(excess_ils / price_ils) if price_ils else 0
            if shares <= 0:
                continue
            out.append({
                "id": _rid("warroom_trim", tk), "dimension": "signal", "severity": "MEDIUM",
                "title": f"Trim {tk} back to your cap",
                "why": (f"The agents approved this on the {s.get('outcome_label')} path, and {tk} "
                        f"is {weight:.0%} of your book against a {cap:.0%} single-name cap. {alpha}").strip(),
                "action": (f"Sell {shares} {tk} (~{_ils(excess_ils)}) to bring it from {weight:.0%} "
                           f"down to {cap:.0%}."),
                "impact": (f"Cuts single-name risk and frees ~{_ils(excess_ils)}."
                           + (f" Signal impact {impact:.0f}/100." if impact is not None else "")),
                "how": [f"Sell {shares} {tk} (~{_ils(excess_ils)})",
                        "Proceeds land in cash, visible on Holdings",
                        "Open Agents -> war room for the full reasoning"],
                "est_amount": round(excess_ils, 2),
                "apply": {"kind": "trim", "ticker": tk, "shares": shares},
                "meta": {"source": "war_room", "ticker": tk, "impact": impact,
                         "confidence": confidence},
            })
            continue

        # A buy candidate: size it to the plan, then say how to pay for it.
        target_w = target.get(cls, 0.0)
        if target_w <= 0:
            continue  # the plan doesn't hold this asset class at all
        room = _fund.size_purchase(nav, weight, min(target_w, cap), cap)
        if room < _fund.MIN_TRADE_ILS:
            continue  # no room without breaching the plan -> not a recommendation
        fund = _fund.plan_funding(rows, snap, plan, objective, cap, room,
                                  cash_ils=cash, exclude={tk})
        buyable = min(room, fund.get("funded_ils") or 0.0)
        if buyable < _fund.MIN_TRADE_ILS:
            continue  # can't be paid for -> don't pretend it's an action
        fund = _fund.plan_funding(rows, snap, plan, objective, cap, buyable,
                                  cash_ils=cash, exclude={tk})
        verb = "Add to" if pos is not None else "Buy"
        out.append({
            "id": _rid("warroom_buy", tk), "dimension": "signal", "severity": "MEDIUM",
            "title": f"{verb} {tk} — {_ils(buyable)}",
            "why": (f"The agents approved this on the {s.get('outcome_label')} path"
                    + (f" at {confidence:.0f}% confidence" if confidence is not None else "")
                    + f". {alpha} It fits your {objective} plan: {cls} is {mix.get(cls, 0.0):.0%} "
                      f"against a {target_w:.0%} target.").strip(),
            "action": (f"{verb} {_ils(buyable)} of {tk}. That takes {cls} from "
                       f"{mix.get(cls, 0.0):.0%} toward your {target_w:.0%} target and keeps {tk} "
                       f"under your {cap:.0%} single-name cap. "
                       + _fund.describe_funding(fund)),
            "impact": (f"Moves {cls} ~{(buyable / nav):.0%} closer to target."
                       + (f" Signal impact {impact:.0f}/100." if impact is not None else "")),
            "how": ([f"{verb} {_ils(buyable)} of {tk}"]
                    + [f"Sell {x['shares']} {x['ticker']} (~{_ils(x['value_ils'])}) — {x['reason']}"
                       for x in fund.get("sells", [])]
                    + ([f"Use {_ils(fund['from_cash_ils'])} of cash (floor of "
                        f"{_ils(fund['cash_floor_ils'])} stays untouched)"]
                       if fund.get("from_cash_ils") else [])
                    + ["Signals are trend-divergence based, not a price forecast"]),
            "est_amount": round(buyable, 2),
            "apply": {"kind": "buy_funded", "ticker": tk,
                      "market": getattr(pos, "market", None) or "NYSE",
                      "asset_class": cls, "amount_ils": round(buyable, 2),
                      "from_cash_ils": fund.get("from_cash_ils", 0.0),
                      "sells": fund.get("sells", [])},
            "meta": {"source": "war_room", "ticker": tk, "impact": impact,
                     "confidence": confidence, "funding": fund},
        })
    return out


async def build_recommendations(session: AsyncSession, user: User) -> dict:
    _tm = _Stopwatch()
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
    try:
        from app.services.intake_service import get_cash as _get_cash
        _cash_ils = await _get_cash(session, user)
    except Exception:  # noqa: BLE001
        _cash_ils = 0.0
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
        # Never harvest the sleeve the plan says to hold.
        #
        # Observed live: this card offered to sell SOXL -- the aggressive leg of
        # the applied btm_trend_soxl strategy, with a max_weight cap armed on it
        # by P1 -- to realize a 12 shekel tax saving. Two agents giving opposite
        # instructions about the same position on the same screen. The tax engine
        # has no idea the position is held on purpose, and a 12 shekel tail
        # should not wag a strategy dog.
        try:
            from app.services.strategy_service import sleeve_targets
            _plan_sid = getattr(plan, "strategy", None) if plan is not None else None
            _sleeve = {t.upper() for t in sleeve_targets(
                _plan_sid, getattr(plan, "strategy_sleeve_pct", None))} if _plan_sid else set()
        except Exception:  # noqa: BLE001
            _sleeve = set()
        _held_for_strategy = sorted({t for t in losers if (t or "").upper() in _sleeve})
        losers = [t for t in losers if (t or "").upper() not in _sleeve]
        save = harvest[0]["estimated_annual_tax_savings_currency"] if losers else 0.0

    if harvest and losers:
        _note = ("" if not _held_for_strategy else
                 f" {', '.join(_held_for_strategy)} is left alone: your plan holds it "
                 f"on purpose, and a tax saving is not a reason to sell your strategy.")

        # Severity by MATERIALITY, and never CRITICAL.
        #
        # This was `"CRITICAL" if save > 0 else "MEDIUM"`, so a 12 shekel saving
        # on a 21,405 shekel book -- 0.06% -- was flagged Important and sorted
        # above everything. CRITICAL is what a firing stop-loss gets (see _SEV);
        # an optional tax optimisation is not in that class, and putting it there
        # invites a careless tap on a card that sells an entire position.
        #
        # The threshold is the app's own materiality unit rather than a number
        # picked here: MIN_TRADE_ILS is what it already considers "too small to
        # be worth the friction". A saving below that is, by the app's own
        # standard, not worth shouting about.
        from app.services.funding_service import MIN_TRADE_ILS as _MIN_TRADE
        _sell_value = sum(float(r.current_price or 0) * float(r.quantity)
                          for r in rows if r.ticker in losers)
        if save >= _MIN_TRADE:
            _sev = "HIGH"
        elif save >= _MIN_TRADE / 5:
            _sev = "MEDIUM"
        else:
            _sev = "LOW"
        # Say what it costs, not only what it saves. Selling 2,001 to save 12 is
        # a decision the user should be able to see the shape of.
        _cost_note = ("" if _sev != "LOW" or not _sell_value else
                      f" Worth weighing: that means selling {_ils(_sell_value)} of "
                      f"holdings to save {_ils(save)}, and giving up the position "
                      f"for the wash-sale window.")
        recs.append({"id": _rid("tax"), "dimension": "tax",
                     "severity": _sev,
                     "title": ("Harvest a tax loss" if _sev != "LOW"
                               else f"A small tax loss to harvest ({_ils(save)})"),
                     "why": "You hold position(s) below what you paid \u2014 selling now realizes a loss you can "
                            "use to offset taxable gains before year-end." + _note,
                     "action": f"Sell your losing position(s) ({', '.join(losers)}) to realize the loss "
                               f"and save about {_ils(save)} in tax this year.",
                     "impact": f"Lowers this year's tax bill by about {_ils(save)}; you can re-buy after the "
                               f"wash-sale window if you still want the exposure." + _cost_note,
                     "how": ["Sell the position(s) currently below what you paid",
                             "The realized loss offsets taxable gains, lowering your tax bill",
                             "If you still believe in them, re-buy after the wash-sale window"],
                     "est_amount": save,
                     "apply": {"kind": "sell_losers", "tickers": losers}})
        recs[-1]["audit_trail"] = audit_for("tax",
            raw_data={"losing_tickers": losers, "excluded_strategy_sleeve": _held_for_strategy,
                      "estimated_annual_tax_savings": round(save, 2)},
            formulas=[
                f("Tax saved", "tax_saved = realized_loss x CGT_rate", result=f"\u20aa{save:,.0f}")])

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
                # A named fund and a number, funded explicitly: "diversify" on its
                # own is a topic, not something you can act on.
                _amt = round(min(0.10, max(0.05, 1.0 - cap)) * nav, 2) if nav else 0.0
                _spec, _how = {"kind": "none"}, [
                    "Favor under-represented regions for your next contributions",
                    "A broad ex-US global ETF (VXUS) is the simplest single fix"]
                _act = ("Spread new money across more regions so one country's downturn can't sink "
                        "the whole portfolio.")
                try:
                    from app.services import funding_service as _fs
                    if _amt >= _fs.MIN_TRADE_ILS:
                        _f = _fs.plan_funding(rows, snap, plan, objective, cap, _amt,
                                              cash_ils=_cash_ils, exclude={"VXUS"})
                        _buy = min(_amt, _f.get("funded_ils") or 0.0)
                        if _buy >= _fs.MIN_TRADE_ILS:
                            _f = _fs.plan_funding(rows, snap, plan, objective, cap, _buy,
                                                  cash_ils=_cash_ils, exclude={"VXUS"})
                            _act = (f"Buy {_ils(_buy)} of VXUS (global ex-US equities) to spread "
                                    f"beyond one region. " + _fs.describe_funding(_f))
                            _how = ([f"Buy {_ils(_buy)} of VXUS (Vanguard Total International Stock)"]
                                    + [f"Sell {x['shares']} {x['ticker']} (~{_ils(x['value_ils'])}) "
                                       f"— {x['reason']}" for x in _f.get("sells", [])]
                                    + ([f"Use {_ils(_f['from_cash_ils'])} of cash"]
                                       if _f.get("from_cash_ils") else [])
                                    + ["Alternatives: VEA (developed) or VWO (emerging)"])
                            _spec = {"kind": "buy_funded", "ticker": "VXUS", "market": "NASDAQ",
                                     "asset_class": "Equities", "amount_ils": round(_buy, 2),
                                     "from_cash_ils": _f.get("from_cash_ils", 0.0),
                                     "sells": _f.get("sells", [])}
                except Exception:  # noqa: BLE001
                    logger.warning("geo diversification sizing failed", exc_info=True)
                recs.append({"id": _rid("divrisk", "geo"), "dimension": "diversification",
                             "severity": a.get("severity", "MEDIUM"),
                             "title": ("Diversify across regions"
                                       + (f" — {_ils(_spec['amount_ils'])} of VXUS"
                                          if _spec["kind"] == "buy_funded" else "")),
                             "why": a.get("detail") or "Most of your money sits in a single region.",
                             "action": _act,
                             "impact": "Lowers geographic concentration risk from over-reliance on one region.",
                             "how": _how,
                             "est_amount": _spec.get("amount_ils"),
                             "apply": _spec})
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
    _tm.mark("holding_verdicts")
    recs += _hedge_recs(rows, snap)
    _tm.mark("hedge")
    recs += _momentum_recs(rows, snap)
    _tm.mark("momentum")
    recs += _income_cost_recs(pdicts, snap, objective)
    _tm.mark("income_cost")
    # Surplus cash gets a sized, executable home. Emitted after the income agent
    # so _reconcile can drop the old advisory "Put idle cash to work" card when
    # this one fires -- two cards about the same shekels is what the reconcile
    # pass exists to prevent.
    _redeploy = _redeploy_cash_recs(rows, snap, plan, objective, cap, _cash_ils)
    recs += _commodity_recs(rows, snap, objective, plan, cap, _cash_ils)
    try:
        async with _agent_tx(session):
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

    # Liquidity floor: name what to sell and how much, rather than "hold more cash".
    try:
        from app.services import funding_service as _fs2
        _floor = _fs2.cash_floor_ils(nav, objective, plan)
        if nav and _cash_ils < _floor:
            _need = round(_floor - _cash_ils, 2)
            _cands = _fs2.rank_trim_candidates(rows, snap, objective, cap)
            if _need >= _fs2.MIN_TRADE_ILS and _cands:
                _c = _cands[0]
                _sh = int(_need / _c["price_ils"]) if _c["price_ils"] else 0
                if _sh > 0:
                    recs.append({
                        "id": _rid("raise_cash"), "dimension": "liquidity", "severity": "MEDIUM",
                        "title": f"Raise {_ils(_need)} of cash",
                        "why": (f"You hold {_ils(_cash_ils)} liquid against a "
                                f"{_fs2.cash_floor_pct(objective, plan):.0%} floor for a {objective} "
                                f"plan ({_ils(_floor)}). Too little dry powder means a forced sale "
                                f"at a bad moment."),
                        "action": (f"Sell {_sh} {_c['ticker']} (~{_ils(_need)}) to rebuild your cash "
                                   f"floor. {_c['reason']}."),
                        "impact": f"Restores your liquidity floor to {_ils(_floor)}.",
                        "how": [f"Sell {_sh} {_c['ticker']} (~{_ils(_need)})",
                                _c["reason"].capitalize(),
                                "Proceeds land in cash, visible on Holdings"],
                        "est_amount": _need,
                        "apply": {"kind": "trim", "ticker": _c["ticker"], "shares": _sh},
                        "meta": {"cash_ils": _cash_ils, "floor_ils": _floor}})
    except Exception:  # noqa: BLE001
        logger.warning("cash-floor recommendation failed", exc_info=True)
        degraded.append("cash_floor")

    # A price nothing has traded against is not a price. The 30-minute refresh
    # "succeeds" every time on a delisted instrument, so the holding looks
    # healthy while its frozen number is counted in NAV -- and therefore in the
    # gain, the allocation mix, the concentration caps and every recommendation
    # sized against NAV. Surfacing it is GUIDANCE, never an automatic write-off:
    # delisted, merged and renamed all look identical from here, and deciding a
    # holding is worthless is not the app's call.
    try:
        _weights = snap.get("exposure_ticker") or {}
        for _p in rows:
            _m = _p.meta if isinstance(_p.meta, dict) else {}
            if not _m.get("price_stale"):
                continue
            _tk = _p.ticker
            _as_of = str(_m.get("price_as_of") or "")[:10] or "an unknown date"
            _days = _m.get("price_stale_days")
            _w = _weights.get(_tk, 0.0) or 0.0
            _val = _w * nav
            _aged = f"{_days} trading days" if _days else "well over a week"
            recs.append({
                "id": _rid("stale_price", _tk), "dimension": "data", "severity": "HIGH",
                "title": f"{_tk} has not traded since {_as_of}",
                "why": (f"Quotes for {_tk} still come back successfully, but the last actual "
                        f"trade was {_as_of} — {_aged} ago. A price with no trades behind it "
                        f"is a leftover, not a market price, and it looks exactly like a "
                        f"holding that simply did not move."),
                "action": (f"Check what happened to {_tk} — delisted, merged, or renamed — then "
                           f"update or remove the holding. InvestWise will not write it off for "
                           f"you, because it cannot tell which of those it is."),
                "impact": (f"{_ils(_val)} ({_w:.0%} of your book) is currently valued at that "
                           f"frozen price, so your gain, allocation mix and anything sized "
                           f"against NAV all rest partly on it."),
                "how": [f"Look up {_tk} at your broker or the exchange",
                        "If it was delisted or bought out, record the actual proceeds as cash",
                        "If it was renamed, edit the holding to the new ticker",
                        "Until then its price is held at the last real trade, not refreshed"],
                "est_amount": round(_val, 2),
                "meta": {"price_as_of": _m.get("price_as_of"),
                         "trading_days": _days, "value_ils": round(_val, 2)},
            })
    except Exception:  # noqa: BLE001
        logger.warning("stale-price recommendations failed", exc_info=True)
        degraded.append("stale_prices")

    # Signals the agent pipeline approved (same decisions the war room shows).
    try:
        async with _agent_tx(session):
            recs += await _war_room_recs(session, user, rows)
    except Exception:  # noqa: BLE001
        logger.warning("war-room recommendations failed", exc_info=True)
        degraded.append("agent_signals")
    _tm.mark("war_room")

    # Trading rules that have fired -> top-priority, user-defined actions.
    try:
        async with _agent_tx(session):
            from app.services.rules_service import triggered_rule_recs
            recs += await triggered_rule_recs(session, user)
    except Exception:  # noqa: BLE001
        logger.warning("triggered trading-rule recommendations failed", exc_info=True)
        degraded.append("trading_rules")

    # The active rule-based strategy changing its mind is the same class of event
    # as a trading rule firing: a discipline the user chose, speaking. Acting
    # late on it is the main reason a rule underperforms its own backtest.
    try:
        async with _agent_tx(session):
            from app.services.strategy_signal_service import discipline_recs, pending_signal_recs
            recs += await pending_signal_recs(session, user)
            recs += await discipline_recs(session, user)
    except Exception:  # noqa: BLE001
        logger.warning("strategy signal recommendations failed", exc_info=True)
        degraded.append("strategy_signals")
    _tm.mark("strategy_signals")

    # Sleeve drift: you chose a size, the market moved you off it.
    #
    # DECIDED, and the distinction matters more than the card: Accept executes
    # the funded rebalance when you ALREADY HOLD some of the sleeve, and refuses
    # when you hold none of it. The +/-5 point band fires at 0% against a chosen
    # 20% too -- but a 20-point gap is not drift, it is never having funded the
    # sleeve, and one tap taking a book from nothing to a fifth in a 3x fund is
    # a different decision from topping up. That one routes to "Fund this
    # sleeve", which shows the whole plan.
    try:
        from app.services.strategy_service import sleeve_targets
        _plan_sid = getattr(plan, "strategy", None) if plan is not None else None
        _chosen = getattr(plan, "strategy_sleeve_pct", None) if plan is not None else None
        _targets = sleeve_targets(_plan_sid, _chosen) if _plan_sid else {}
        if _targets and nav:
            _weights = snap.get("exposure_ticker") or {}
            for _tk, _w in sorted(_targets.items()):
                _tk = _tk.upper()
                _target_pct = _w * 100.0
                _actual_pct = (_weights.get(_tk, 0.0) or 0.0) * 100.0
                _gap = _target_pct - _actual_pct
                if abs(_gap) < SLEEVE_DRIFT_BAND_PCT:
                    continue
                _held = _actual_pct > 0.05
                _amount = abs(_gap) / 100.0 * nav
                _cold = not _held and _gap > 0
                if _cold:
                    recs.append({
                        "id": _rid("sleeve_coldstart", _tk), "dimension": "strategy",
                        "severity": "MEDIUM",
                        "title": f"You chose a {_target_pct:.0f}% {_tk} sleeve and hold none of it",
                        "why": (f"Your plan says {_target_pct:.0f}% of the book in {_tk}; "
                                f"you hold nothing. That is not drift -- the sleeve has "
                                f"never been funded."),
                        "action": ("Use \u201cFund this sleeve\u201d on the Plan tab. It shows "
                                   "which positions would be sold, for how much, and the "
                                   "estimated tax, before anything happens."),
                        "impact": (f"Closing it means moving about {_ils(_amount)} into {_tk}. "
                                   f"Starting a position is a different decision from "
                                   f"correcting a drift, so this card will not do it in one tap."),
                        "how": ["Plan tab \u2192 the strategy card \u2192 Fund this sleeve",
                                "Review every funding leg and its tax, then confirm",
                                "No brokerage order is placed either way"],
                        "est_amount": round(_amount, 2),
                        "meta": {"chosen_pct": round(_target_pct, 1),
                                 "actual_pct": round(_actual_pct, 1), "cold_start": True},
                    })
                    continue
                _over = _gap < 0
                recs.append({
                    "id": _rid("sleeve_drift", _tk), "dimension": "strategy",
                    "severity": "MEDIUM",
                    "title": (f"{_tk} has drifted to {_actual_pct:.0f}% of a "
                              f"{_target_pct:.0f}% sleeve"),
                    "why": (f"You chose {_target_pct:.0f}%. The market has moved you to "
                            f"{_actual_pct:.0f}%, which is "
                            f"{abs(_gap):.0f} points {'above' if _over else 'below'} it."),
                    "action": ((f"Trim about {_ils(_amount)} of {_tk} back to your "
                                f"{_target_pct:.0f}% sleeve.") if _over else
                               (f"Add about {_ils(_amount)} of {_tk} to get back to your "
                                f"{_target_pct:.0f}% sleeve.")),
                    "impact": ("Puts the sleeve back at the size you chose. Accept executes "
                               "this against your tracked book; no brokerage order is placed."),
                    "how": [f"Accept rebalances {_tk} toward {_target_pct:.0f}%",
                            "Every funding leg and its estimated tax is named first",
                            "The real trade is yours to place at your broker"],
                    "est_amount": round(_amount, 2),
                    "apply": ({"kind": "trim", "ticker": _tk,
                               "shares": max(1, int(_amount / max(
                                   1e-9, next((float(r.current_price or 0) for r in rows
                                               if (r.ticker or "").upper() == _tk), 0) or 1e-9)))}
                              if _over else
                              {"kind": "fund_sleeve", "strategy_id": _plan_sid,
                               "sleeve_pct": _chosen}),
                    "meta": {"chosen_pct": round(_target_pct, 1),
                             "actual_pct": round(_actual_pct, 1), "cold_start": False},
                })
    except Exception:  # noqa: BLE001
        logger.warning("sleeve-drift recommendation failed", exc_info=True)
        degraded.append("sleeve_drift")

    # Protective rules that are suggested but not yet armed ARE "what to do now"
    # by this app's own definition, so they belong on Today. They were reachable
    # only through GET /rules/suggestions, which feeds a panel on a
    # settings-shaped page -- found only by someone already looking for it. One
    # card summarising the set, never one card per rule.
    try:
        async with _agent_tx(session):
            from app.services.rules_service import suggest_rules_for_holdings
            _sugg = await suggest_rules_for_holdings(session, user)
        _flat = [{**r, "ticker": r.get("ticker") or h["ticker"]}
                 for h in (_sugg or []) for r in (h.get("rules") or [])]
        if _flat:
            _specs = [{"ticker": r["ticker"], "rule_type": r["rule_type"],
                       "mode": r.get("mode", "pct"), "level": r["level"],
                       "note": r.get("why")} for r in _flat]
            _names = sorted({r["ticker"] for r in _flat})
            _lines = [f"{r['ticker']}: {r.get('label') or r['rule_type']}" for r in _flat[:8]]
            recs.append({
                # NOT dimension "rule". That is reserved for a TradingRule that
                # has actually FIRED -- the server tracks those by a `rule_<id>`
                # card id, and the banner reconciliation counts them. This card
                # is a suggestion, nothing has fired, and labelling it "rule"
                # made it count toward "N rules triggered" while the server
                # correctly ignored it: banner 1, cards 2, and a FAIL in three
                # separate smoke sections.
                "id": _rid("rules_suggested"), "dimension": "discipline", "severity": "MEDIUM",
                "title": (f"{len(_flat)} protective rules ready to arm"
                          if len(_flat) > 1 else "1 protective rule ready to arm"),
                "why": ("Every level below is derived from the holding's own realized "
                        "volatility, not a round number — a stop inside the noise is churn, "
                        "not discipline. Unarmed, none of them does anything."),
                "action": (f"Arm {len(_flat)} suggested rule(s) across "
                           f"{', '.join(_names)}. Accept arms all of them at once; "
                           f"the Rules tab lets you fine-tune or add them one by one."),
                "impact": ("Each rule then watches its holding on every price refresh and "
                           "raises an alert plus a Today card when it fires."),
                "how": _lines + ["Accept arms all of these as real trading rules",
                                 "No brokerage order is placed — a firing is an alert, "
                                 "and acting on it is yours to do"],
                "apply": {"kind": "create_rules", "rules": _specs},
                "meta": {"count": len(_flat), "tickers": _names},
            })
    except Exception:  # noqa: BLE001
        logger.warning("suggested-rule recommendations failed", exc_info=True)
        degraded.append("rule_suggestions")

    # Cash reconciliation runs HERE, after every agent has contributed -- not at
    # the point the redeploy card is built. Placing it earlier meant the filter
    # ran before _war_room_recs existed, so production showed "Redeploy ₪3,884"
    # (₪971 into SCHD) alongside "Add to SCHD — ₪7,899" and left the user to
    # work out which one to follow. One pot of money, one card.
    if _redeploy:
        _legs = {x["ticker"] for x in _redeploy[0]["apply"]["legs"]}

        def _competes(r: dict) -> bool:
            if r.get("id") == _rid("cashdrag"):
                return True
            _ap = r.get("apply") or {}
            if _ap.get("kind") == "rebalance_to_objective":
                return True
            # Any sized buy of a ticker the redeploy card already funds, whoever
            # emitted it (war room, commodities, momentum...).
            return _ap.get("kind") in ("buy_funded", "buy") and _ap.get("ticker") in _legs
        recs = [r for r in recs if not _competes(r)]
        recs += _redeploy

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
        # Self-heal a deadlock before suppressing: a rule can sit latched
        # `triggered` while its card is hidden by an earlier dismissal, so the
        # red "N trading rules triggered" banner counts work with nothing left to
        # click. Any rule acted on before rule-clearing shipped is in exactly
        # that state, and would stay there for the life of the rule. Being
        # suppressed means the user already dealt with it, so resolve it.
        _hidden_rules = {r.get("id") for r in recs
                         if str(r.get("id", "")).startswith("rule_")} & (dismissed | completed)
        if _hidden_rules:
            from app.services.rules_service import resolve_rule
            for _hid in _hidden_rules:
                try:
                    await resolve_rule(session, user, _hid[5:], "acknowledged")
                except Exception:  # noqa: BLE001 -- never break Today over cleanup
                    logger.warning("could not self-heal rule %s", _hid, exc_info=False)
        # A signal the user already dealt with must not stay pending, or it
        # reappears every morning until it is acted on -- the exact nagging the
        # flip-only design exists to avoid.
        _hidden_sig = {r.get("id") for r in recs
                       if str(r.get("id", "")).startswith("stratsig_")} & (dismissed | completed)
        if _hidden_sig:
            from app.services.strategy_signal_service import active_strategy_id, resolve_signal
            try:
                _sid = await active_strategy_id(session, user)
                if _sid:
                    await resolve_signal(session, user, _sid)
            except Exception:  # noqa: BLE001 -- never break Today over cleanup
                logger.warning("could not clear a handled strategy signal", exc_info=False)
        _n = len(recs)
        recs = [r for r in recs if r.get("id") not in dismissed]
        suppressed = _n - len(recs)
        _n = len(recs)
        recs = [r for r in recs if r.get("id") not in completed]
        done_count = _n - len(recs)

    # FINAL RECONCILIATION. The red banner counts rules whose `triggered` flag is
    # set; the cards come from triggered_rule_recs. Two sources, so they can
    # drift for any reason -- and every reason ends the same way: a banner
    # counting work with nothing left to click, forever, because only a card can
    # clear it. The heal above fixes the case where a card existed and was
    # hidden. This catches every other case, whatever caused it, by asserting
    # the invariant directly: a triggered rule with no visible card is a
    # contradiction, so resolve it.
    #
    # Guarded on the rules agent having succeeded. If it degraded, the absence
    # of cards says nothing about the rules and retiring them would destroy real
    # pending work over a transient provider failure.
    rule_banner = {"triggered": [], "carded": [], "healed": [], "skipped_reason": None}
    if "trading_rules" in degraded:
        # Say so, rather than leaving a silent no-op. A banner that disagrees
        # with the cards while the rules agent is down looks identical to a
        # reconciliation that ran and failed, and that ambiguity cost several
        # wrong hypotheses about exactly this failure.
        rule_banner["skipped_reason"] = (
            "the trading-rules agent degraded, so missing cards say nothing "
            "about the rules and nothing was retired")
    if "trading_rules" not in degraded:
        try:
            from app.services.rules_service import resolve_rule
            from sqlalchemy import select as _select

            from app.models.tables import TradingRule as _TR
            _live = (await session.scalars(
                _select(_TR).where(_TR.subject == user.email, _TR.active.is_(True),
                                   _TR.triggered.is_(True)))).all()
            _carded = {str(r.get("id", ""))[5:] for r in recs
                       if str(r.get("id", "")).startswith("rule_")}
            for _r in _live:
                _short = str(_r.id)[:8]
                rule_banner["triggered"].append(_short)
                if _short in _carded:
                    rule_banner["carded"].append(_short)
                    continue
                try:
                    await resolve_rule(session, user, _short, "acknowledged",
                                       {"reason": "triggered with no visible card"})
                    rule_banner["healed"].append(_short)
                    logger.info("healed a rule triggered with no card: %s %s",
                                _r.ticker, _short)
                except Exception:  # noqa: BLE001 -- never break Today over cleanup
                    logger.warning("could not heal orphaned rule %s", _short, exc_info=False)
        except Exception:  # noqa: BLE001
            logger.warning("rule banner reconciliation failed", exc_info=False)

    recs.sort(key=lambda r: _SEV.get(r["severity"], 9))
    _tm.mark("reconcile_and_filter")
    # Phase 3.3: validate the Risk Agent's beta against history before surfacing.
    bt_holdings = [{"ticker": d["ticker"], "asset_class": d.get("asset_class") or "Equities",
                    "value_ils": d["quantity"] * d["current_price"]} for d in pdicts]
    bt = BacktestEngine().run(bt_holdings, portfolio_vol_pct=snap["avg_volatility_pct"])
    _tm.mark("backtest")
    _ideas = _buy_ideas(snap)      # screener; timed separately, it hits providers
    _tm.mark("buy_ideas")
    return {"count": len(recs), "objective": objective, "recommendations": recs[:12],
            "market": market,
            # Honesty signals for the Today empty state: "nothing to do",
            # "you ignored everything" and "an agent failed" are three different things.
            "dismissed_count": suppressed,
            "completed_count": done_count,
            "degraded": degraded, "rule_banner": rule_banner,
            # Where the time actually went, per agent (ms). Cheap to compute and
            # it turns "the endpoint is slow" into a specific culprit.
            "timings_ms": {**_tm.marks, "slowest": _tm.slowest()},
            "buy_ideas": _ideas,
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
        s += 100.0 if 0 < f.pe <= 15 else (60.0 if 0 < f.pe <= 30 else (20.0 if f.pe > 0 else 0.0))
        n += 1
    if f.earnings_growth_pct is not None:
        s += max(0.0, min(100.0, 50.0 + f.earnings_growth_pct * 2.0))
        n += 1
    if f.roe_pct is not None:
        s += max(0.0, min(100.0, f.roe_pct * 2.5))
        n += 1
    if f.profit_margin_pct is not None:
        s += max(0.0, min(100.0, 50.0 + f.profit_margin_pct * 1.5))
        n += 1
    if f.debt_to_equity is not None:
        s += max(0.0, min(100.0, 100.0 - f.debt_to_equity / 2.5))
        n += 1
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


def _redeploy_cash_recs(rows, snap, plan, objective, cap, cash_ils) -> list[dict]:
    """Surplus cash -> one sized, executable redeployment plan.

    After a stop-loss fires and you accept it, the proceeds land in cash and the
    portfolio drifts away from its plan. The old "Put idle cash to work" card
    noticed this but was `apply: none` -- it said "move the rest into your target
    mix" without naming what, sizing it, or being able to do it.

    Allocation is derived, never invented:
      * spendable  = cash above the objective's floor (Preserve 10% .. Grow 3%);
      * candidates = every asset class under its target weight, filled first by
        the holdings you already own that sit below their share of that class,
        then by screener picks for classes with no representation at all;
      * each leg is clipped by the single-name concentration cap, and legs below
        the minimum trade size are dropped rather than rounded up.
    """
    from app.services import funding_service as _fund

    nav = snap.get("nav") or 0.0
    if not nav or cash_ils <= 0:
        return []
    spendable = _fund.spendable_cash(cash_ils, nav, objective, plan)
    if spendable < _fund.MIN_TRADE_ILS:
        return []

    mix, _ = current_mix(rows)
    target = OBJ_TARGET.get(objective, OBJ_TARGET["Balanced"])
    # Weights are computed from the rows themselves rather than read out of
    # snap["exposure_ticker"]. That lookup silently returned 0 for every ticker,
    # so size_purchase saw "current weight 0" for a name at 29% and handed every
    # leg an identical unclipped slice -- proposing to buy MORE of a holding
    # already past the concentration cap. A self-contained calculation cannot
    # drift from whatever keys the caller's snapshot happens to use.
    from app.services.fx import fx_rate as _fxr, price_currency as _pcy
    weights: dict[str, float] = {}
    for r in rows:
        _m = getattr(r, "meta", None)
        _rate = _fxr(_pcy(getattr(r, "market", None), _m if isinstance(_m, dict) else None))
        _v = float(getattr(r, "quantity", 0) or 0) * float(getattr(r, "current_price", 0) or 0) * _rate
        weights[r.ticker] = weights.get(r.ticker, 0.0) + (_v / nav if nav else 0.0)
    held_by_class: dict[str, list] = {}
    for r in rows:
        # The meta asset_class must be passed: classify() falls back to ticker
        # heuristics otherwise, and "CASH" matches none of them -- so the cash
        # row itself would land in Equities and become a top-up target, i.e. the
        # app proposing to buy cash with cash.
        _meta = getattr(r, "meta", None)
        cls = classify(r.ticker, getattr(r, "market", None),
                       (_meta or {}).get("asset_class") if isinstance(_meta, dict) else None)
        if cls and cls.lower() != "cash":
            held_by_class.setdefault(cls, []).append(r)

    # Gaps per asset class, largest shortfall first.
    gaps = []
    for cls, tw in (target or {}).items():
        if cls.lower() == "cash" or not tw:
            continue
        gap_ils = max(0.0, (float(tw) - float(mix.get(cls, 0.0)))) * nav
        if gap_ils >= _fund.MIN_TRADE_ILS:
            gaps.append((cls, gap_ils, float(tw)))
    if not gaps:
        return []
    gaps.sort(key=lambda g: g[1], reverse=True)

    total_gap = sum(g[1] for g in gaps)
    legs: list[dict] = []
    unplaced: list[dict] = []
    remaining = spendable
    for cls, gap_ils, tw in gaps:
        if remaining < _fund.MIN_TRADE_ILS:
            break
        # Proportional share of the surplus, never more than the class actually needs.
        budget = min(gap_ils, round(spendable * (gap_ils / total_gap), 2), remaining)
        held = held_by_class.get(cls) or []
        if held:
            # Top up what you already own, weakest weight first.
            held.sort(key=lambda p: weights.get(p.ticker, 0.0))
            per = round(budget / len(held), 2)
            for p in held:
                if remaining < _fund.MIN_TRADE_ILS:
                    break
                room = _fund.size_purchase(nav, weights.get(p.ticker, 0.0), cap, cap)
                amt = round(min(per, room, remaining), 2)
                if amt < _fund.MIN_TRADE_ILS:
                    continue
                legs.append({"ticker": p.ticker, "amount_ils": amt, "asset_class": cls,
                             "reason": f"{cls} is {mix.get(cls, 0.0):.0%} vs a {tw:.0%} target"})
                remaining = round(remaining - amt, 2)
        else:
            # The plan wants this class and you hold none of it -- a real gap, so
            # a new name is warranted rather than concentrating further.
            pick = next((b for b in _buy_ideas(snap)
                         if classify(b.get("ticker"), None) == cls), None)
            amt = round(min(budget, remaining), 2)
            if pick and amt >= _fund.MIN_TRADE_ILS:
                legs.append({"ticker": pick["ticker"], "amount_ils": amt, "asset_class": cls,
                             "reason": f"you hold no {cls}; the plan targets {tw:.0%}",
                             "new_position": True})
                remaining = round(remaining - amt, 2)
            else:
                # Nothing to buy for a class the plan wants but you don't hold
                # and the screener can't fill. Record it -- the budget must not
                # just evaporate into idle cash without explanation.
                unplaced.append({"asset_class": cls, "amount_ils": round(min(budget, remaining), 2),
                                 "reason": f"you hold no {cls} and no candidate was available"})

    # Second pass: a leg that could not be placed left its share of the surplus
    # unspent. Live, the Fixed Income leg was undeployable (plan targets 10%,
    # nothing held, no screener pick), so ~1,940 stayed in cash while the card
    # reported success -- 12% cash against a 3% floor. Push what is left into the
    # classes that CAN absorb it, still respecting the single-name cap.
    if remaining >= _fund.MIN_TRADE_ILS and legs:
        topups = [x for x in legs if not x.get("new_position")]
        for x in sorted(topups, key=lambda leg: weights.get(leg["ticker"], 0.0)):
            if remaining < _fund.MIN_TRADE_ILS:
                break
            already = x["amount_ils"]
            room = _fund.size_purchase(nav, weights.get(x["ticker"], 0.0), cap, cap) - already
            extra = round(min(remaining, max(0.0, room)), 2)
            if extra < _fund.MIN_TRADE_ILS:
                continue
            x["amount_ils"] = round(already + extra, 2)
            x["reason"] += " (incl. budget reallocated from an unfillable class)"
            remaining = round(remaining - extra, 2)

    if not legs:
        return []
    deployed = round(sum(x["amount_ils"] for x in legs), 2)
    kept = round(cash_ils - deployed, 2)
    floor_pct = _fund.cash_floor_pct(objective, plan)
    new_names = [x["ticker"] for x in legs if x.get("new_position")]

    return [{
        "id": _rid("redeploy", round(deployed)), "dimension": "allocation", "severity": "HIGH",
        "title": f"Redeploy {_ils(deployed)} of idle cash",
        "why": (f"You're holding {_ils(cash_ils)} ({cash_ils / nav:.0%} of the portfolio) against a "
                f"{floor_pct:.0%} floor for a {objective or 'Balanced'} plan. Cash earns nothing "
                f"toward your goal, and the sale proceeds left your mix off-plan."),
        "action": ("Put it back to work: "
                   + "; ".join(f"{_ils(x['amount_ils'])} into {x['ticker']}" for x in legs)
                   + f". Keeps {_ils(kept)} as your buffer."),
        "impact": (f"Moves every underweight asset class toward its target and cuts cash from "
                   f"{cash_ils / nav:.0%} to {kept / nav:.0%}."
                   + (f" Adds {', '.join(new_names)} to fill a gap the plan needs."
                      if new_names else "")),
        "how": ([f"Buy {_ils(x['amount_ils'])} of {x['ticker']} — {x['reason']}" for x in legs]
                + [f"⚠ {_ils(u['amount_ils'])} could not be placed — {u['reason']}"
                   for u in unplaced if u["amount_ils"] >= _fund.MIN_TRADE_ILS]
                + [f"Keep {_ils(kept)} in cash ({floor_pct:.0%} floor for {objective or 'Balanced'})",
                   "Accept executes every leg at live prices, funded entirely from cash",
                   "Tracked book only — no brokerage order is placed"]),
        "est_amount": deployed,
        "apply": {"kind": "redeploy_cash", "legs": legs},
        "meta": {"cash_before_ils": round(cash_ils, 2), "cash_after_ils": kept,
                 "floor_pct": floor_pct,
                 # What the plan asked for but the book could not absorb. Silence
                 # here is what let ~1,940 sit idle while the card read as a
                 # complete answer.
                 "unplaced": [u for u in unplaced if u["amount_ils"] >= _fund.MIN_TRADE_ILS]},
    }]


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


def _commodity_recs(rows, snap, objective, plan=None, cap=0.25, cash_ils=0.0) -> list[dict]:
    """Recommend a specific commodity, sized to the plan gap and funded explicitly."""
    out: list[dict] = []
    try:
        from app.services import funding_service as _fund
    except Exception:  # noqa: BLE001
        _fund = None
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
    pick = picks[0] if picks else None
    pick_tk = getattr(pick, "ticker", None) or "DBC"
    pick_name = getattr(pick, "name", "") or "a broad commodity basket"
    alts = ", ".join(names[1:3]) if len(names) > 1 else "GLD (gold) or DBA (agriculture)"
    gap_ils = round(max(0.0, target - com_w) * nav, 2)
    action = (f"Buy {_ils(gap_ils)} of {pick_tk} ({pick_name}) — that lifts commodities from "
              f"{com_w:.0%} to your {target:.0%} target. Alternatives if you'd rather: {alts}.")
    how = [f"Buy {_ils(gap_ils)} of {pick_tk} ({pick_name})"]
    apply_spec = {"kind": "none"}
    if _fund is not None and gap_ils >= _fund.MIN_TRADE_ILS:
        fund = _fund.plan_funding(rows, snap, plan, objective, cap, gap_ils,
                                  cash_ils=cash_ils, exclude={pick_tk})
        affordable = min(gap_ils, fund.get("funded_ils") or 0.0)
        if affordable >= _fund.MIN_TRADE_ILS:
            fund = _fund.plan_funding(rows, snap, plan, objective, cap, affordable,
                                      cash_ils=cash_ils, exclude={pick_tk})
            action = (f"Buy {_ils(affordable)} of {pick_tk} ({pick_name}) — lifting commodities "
                      f"from {com_w:.0%} toward your {target:.0%} target. "
                      + _fund.describe_funding(fund)
                      + f" Alternatives: {alts}.")
            how = ([f"Buy {_ils(affordable)} of {pick_tk} ({pick_name})"]
                   + [f"Sell {x['shares']} {x['ticker']} (~{_ils(x['value_ils'])}) — {x['reason']}"
                      for x in fund.get("sells", [])]
                   + ([f"Use {_ils(fund['from_cash_ils'])} of cash"] if fund.get("from_cash_ils") else []))
            apply_spec = {"kind": "buy_funded", "ticker": pick_tk, "market": "NYSE",
                          "asset_class": "Commodities", "amount_ils": round(affordable, 2),
                          "from_cash_ils": fund.get("from_cash_ils", 0.0),
                          "sells": fund.get("sells", [])}
            gap_ils = affordable
    out.append({"id": _rid("commodity_add"), "dimension": "diversification", "severity": "LOW",
                "title": f"Add a commodities sleeve — {_ils(gap_ils)} of {pick_tk}",
                "action": action,
                "why": (f"Commodities tend to move differently from equities and bonds, so a small "
                        f"allocation cushions the portfolio when stocks and bonds fall together — "
                        f"your {objective or 'Balanced'} target holds ~{target:.0%} and you hold {com_w:.0%}."),
                "impact": "Adds a low-correlation diversifier, smoothing your overall swings.",
                "how": how,
                "est_amount": gap_ils, "apply": apply_spec,
                "meta": {"commodity_weight": round(com_w, 4), "target": target,
                         "picks": [p.ticker for p in picks], "chosen": pick_tk}})
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
    elif kind == "sell_position":
        # A stop-loss / trailing stop / take-profit is a full exit by definition.
        # The position leaves the book and the net-of-CGT proceeds land in cash,
        # so the money is visible and redeployable rather than vanishing.
        p = by_ticker.get(spec["ticker"])
        if p:
            shares = min(float(spec.get("shares") or 0) or float(p.quantity), float(p.quantity))
            gross, tax = _sale_value_ils(shares, float(p.current_price or 0),
                                         float(p.cost_basis or 0), p.market, p.meta)
            remaining = max(0.0, float(p.quantity) - shares)
            if remaining <= 0:
                await delete_position(session, user, p.ticker, p.market)
            else:
                await update_position(session, user, str(p.id), quantity=remaining)
            credited = await credit_cash(session, user, gross - tax)
            detail = {"sold": [spec["ticker"]], "shares": round(shares, 6),
                      "cash_added_ils": round(credited, 2), "tax_ils": round(tax, 2),
                      "broker_note": "Tracked book updated — no brokerage order was placed."}
        else:
            detail = {"sold": [], "note": f"{spec['ticker']} is no longer held."}
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
    elif kind == "buy_funded":
        detail = await _apply_buy_funded(session, user, spec)
    elif kind == "redeploy_cash":
        detail = await _apply_redeploy_cash(session, user, spec)
    elif kind == "fund_sleeve":
        # Reuses the P0.1 funding path, so there is ONE implementation of "raise
        # this much and buy the sleeve" rather than a second that could size it
        # differently from the button on the Plan tab.
        from app.services.strategy_service import load_basket
        detail = await load_basket(session, user, spec["strategy_id"],
                                   sleeve_pct=spec.get("sleeve_pct"), mode="fund")
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
    # A rule-driven card closes the loop on its own audit trail: the firing that
    # produced this card is stamped with what was actually done.
    rule_id = spec.get("rule_id")
    if rule_id:
        from app.services.rules_service import resolve_rule
        # Stamps the audit event AND clears/consumes the rule, so the "N trading
        # rules triggered" banner reflects what's still outstanding rather than
        # everything that has ever fired.
        await resolve_rule(
            session, user, rule_id,
            "executed" if kind in _ACTIONABLE_KINDS else "acknowledged",
            {"kind": kind, **detail} if detail else {"kind": kind})

    # kind == "none" -> acknowledged only; say so rather than claiming an edit
    if kind not in _ACTIONABLE_KINDS:
        return {"applied": "none", "advisory": True, "title": rec["title"],
                "note": "Marked as done. This one is guidance -- nothing was bought or sold."}
    return {"applied": kind, "title": rec["title"], **detail}



async def _apply_redeploy_cash(session: AsyncSession, user: User, spec: dict) -> dict:
    """Buy every leg straight out of cash, at live prices.

    Cash is debited leg by leg as we go, so a price move between building the
    card and accepting it can only shorten the list -- it can never spend money
    that isn't there.
    """
    from decimal import Decimal

    from app.providers.registry import guarded_quote
    from app.schemas.intake import IntakePosition
    from app.services.fx import fx_rate
    from app.services.intake_service import (
        ensure_account, ensure_entity, get_cash, set_cash, upsert_positions,
    )

    cash = await get_cash(session, user)
    rows = await list_positions(session, user)
    by_ticker = {p.ticker: p for p in rows}
    entity = await ensure_entity(session, user, "Personal", "Personal")
    account = await ensure_account(session, entity, "Main")

    bought, skipped = [], []
    for leg in (spec.get("legs") or []):
        tk, amount = leg.get("ticker"), float(leg.get("amount_ils") or 0)
        if not tk or amount <= 0:
            continue
        if amount > cash:
            skipped.append({"ticker": tk, "reason": "not enough cash left"})
            continue
        try:
            q = guarded_quote(tk)
        except Exception:  # noqa: BLE001
            q = None
        if q is None or not q.price:
            skipped.append({"ticker": tk, "reason": "no live price"})
            continue
        price_ils = float(q.price) * fx_rate(getattr(q, "currency", None) or "USD")
        if price_ils <= 0:
            skipped.append({"ticker": tk, "reason": "no live price"})
            continue
        shares = round(amount / price_ils, 4)
        if shares <= 0:
            skipped.append({"ticker": tk, "reason": "amount too small for one share"})
            continue
        existing = by_ticker.get(tk)
        if existing is not None:
            # Blend the basis so gain/loss stays honest after topping up.
            old_qty, old_basis = float(existing.quantity), float(existing.cost_basis or 0)
            new_qty = old_qty + shares
            existing.quantity = Decimal(str(round(new_qty, 6)))
            if new_qty > 0:
                existing.cost_basis = Decimal(str(round(
                    (old_qty * old_basis + shares * float(q.price)) / new_qty, 6)))
            existing.current_price = Decimal(str(q.price))
        else:
            await upsert_positions(session, user, entity.name, "Personal", account.name, [
                IntakePosition(ticker=tk, market=getattr(q, "market", None) or "NASDAQ",
                               quantity=shares, cost_basis=float(q.price),
                               spot_price=float(q.price)),
            ])
        cash = round(cash - amount, 2)
        bought.append({"ticker": tk, "shares": shares, "amount_ils": round(amount, 2),
                       "price": float(q.price), "new_position": existing is None})
    await set_cash(session, user, cash)
    await session.commit()
    return {"bought": bought, "skipped": skipped, "cash_remaining_ils": round(cash, 2),
            "broker_note": "Tracked book updated — no brokerage order was placed."}


async def _apply_buy_funded(session: AsyncSession, user: User, spec: dict) -> dict:
    """Execute a sized buy, funding it from cash and/or the named trims.

    The sells happen first so the cash is really there, and each leg is priced
    live rather than assumed. Nothing is bought that can't be funded.
    """
    from app.providers.registry import guarded_quote
    from app.schemas.intake import IntakePosition
    from app.schemas.state_machine import Market
    from app.services.fx import fx_rate
    from app.services.intake_service import (
        ensure_account, ensure_entity, get_cash, set_cash, upsert_positions,
    )

    sold, tax_total, raised = [], 0.0, 0.0
    rows = await list_positions(session, user)
    by_ticker = {(p.ticker or "").upper(): p for p in rows}
    for leg in spec.get("sells") or []:
        p = by_ticker.get((leg.get("ticker") or "").upper())
        if p is None:
            continue
        shares = min(float(leg.get("shares") or 0), float(p.quantity))
        if shares <= 0:
            continue
        gross, tax = _sale_value_ils(shares, float(p.current_price or 0),
                                     float(p.cost_basis or 0), p.market, p.meta)
        remaining = float(p.quantity) - shares
        if remaining <= 0:
            await delete_position(session, user, p.ticker, p.market)
        else:
            await update_position(session, user, str(p.id), quantity=remaining)
        raised += max(0.0, gross - tax)
        tax_total += tax
        sold.append(f"{int(shares)} {p.ticker}")

    cash_before = await get_cash(session, user)
    budget = round(float(spec.get("from_cash_ils") or 0.0) + raised, 2)
    if budget <= 0:
        return {"bought": None, "note": "Nothing could be funded, so nothing was bought."}

    ticker = (spec.get("ticker") or "").upper()
    try:
        q = guarded_quote(ticker)
        price, ccy = float(q.price), (getattr(q, "currency", None) or "USD")
    except Exception:  # noqa: BLE001 — no live price: keep the money as cash, don't misprice
        await set_cash(session, user, cash_before + raised)
        return {"sold": sold, "tax_ils": round(tax_total, 2), "bought": None,
                "cash_added_ils": round(raised, 2),
                "note": f"Sold {', '.join(sold) or 'nothing'}; couldn't price {ticker}, held as cash."}
    if price <= 0:
        await set_cash(session, user, cash_before + raised)
        return {"sold": sold, "tax_ils": round(tax_total, 2), "bought": None,
                "cash_added_ils": round(raised, 2),
                "note": f"No live price for {ticker}; proceeds held as cash."}

    native = budget / (fx_rate(ccy) or 1.0)
    qty = native / price
    mk = spec.get("market") if spec.get("market") in {m.value for m in Market} else "NYSE"
    ip = IntakePosition(ticker=ticker, market=Market(mk), depth=1, spot_price=price,
                        listing_price=price, quantity=qty, cost_basis=price,
                        asset_class=spec.get("asset_class") or "Equities")
    entity = await ensure_entity(session, user, "Personal", "Personal")
    account = await ensure_account(session, entity, "Main")
    await upsert_positions(session, account, [ip])
    await session.commit()
    # Cash pays only its share; sale proceeds went straight into the buy.
    await set_cash(session, user, max(0.0, cash_before - float(spec.get("from_cash_ils") or 0.0)))
    return {"sold": sold, "tax_ils": round(tax_total, 2), "bought": ticker,
            "shares": round(qty, 4), "value_ils": round(budget, 2)}


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
