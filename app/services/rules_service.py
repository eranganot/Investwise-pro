"""Trading rules: user-defined stop-loss / take-profit / trailing-stop / price
alerts / buy-the-dip / max-weight. The app never executes trades - a triggered
rule notifies (push) and surfaces a recommended action in 'What to do now'.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import TradingRule, User
from app.services.portfolio_analytics import compute_snapshot, load_positions

RULE_TYPES = {"stop_loss", "take_profit", "trailing_stop", "price_above",
              "price_below", "buy_dip", "max_weight"}

_SEV = {"stop_loss": "CRITICAL", "trailing_stop": "CRITICAL", "max_weight": "HIGH",
        "take_profit": "HIGH", "buy_dip": "HIGH", "price_above": "MEDIUM",
        "price_below": "MEDIUM"}


def _now():
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Position lookup
# --------------------------------------------------------------------------- #
async def _positions_index(session: AsyncSession, user: User) -> dict[str, dict]:
    positions = await load_positions(session, user)
    snap = compute_snapshot(positions) if positions else {"nav": 0, "exposure_ticker": {}}
    nav = snap.get("nav") or 0
    weights = snap.get("exposure_ticker") or {}
    out = {}
    for p in positions:
        tk = p["ticker"].upper()
        out[tk] = {"price": float(p.get("current_price") or 0),
                   "cost": float(p.get("cost_basis") or 0),
                   "weight_pct": round((weights.get(p["ticker"], 0) or 0) * 100, 1)}
    return out


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
def _evaluate(rule: TradingRule, cur: float, cost: float, weight: float) -> tuple[bool, str, str, float | None]:
    """Return (hit, title, action, target)."""
    rt, mode, L, tk = rule.rule_type, rule.mode, rule.level, rule.ticker
    if cur <= 0:
        return (False, "", "", None)
    if rt == "stop_loss":
        target = L if mode == "price" else (cost * (1 - abs(L) / 100) if cost else 0)
        return (cur <= target, f"🛑 {tk} hit your stop-loss",
                f"Consider selling {tk} to cap the loss — now {cur:.2f}, stop {target:.2f}.", target)
    if rt == "take_profit":
        target = L if mode == "price" else (cost * (1 + abs(L) / 100) if cost else 0)
        return (cur >= target and target > 0, f"🎯 {tk} hit your take-profit",
                f"Consider trimming/selling {tk} to lock the gain — now {cur:.2f}, target {target:.2f}.", target)
    if rt == "trailing_stop":
        peak = rule.peak_price or cur
        target = peak * (1 - abs(L) / 100)
        return (cur <= target, f"📉 {tk} hit your trailing stop",
                f"{tk} is {abs(L):.0f}% off its peak ({peak:.2f}) — consider selling to lock gains (now {cur:.2f}).", target)
    if rt == "price_above":
        return (cur >= L, f"🔔 {tk} rose above {L:.2f}", f"{tk} reached {cur:.2f} (your alert ≥ {L:.2f}).", L)
    if rt == "price_below":
        return (cur <= L, f"🔔 {tk} fell below {L:.2f}", f"{tk} is {cur:.2f} (your alert ≤ {L:.2f}).", L)
    if rt == "buy_dip":
        target = L if mode == "price" else (cost * (1 - abs(L) / 100) if cost else 0)
        return (cur <= target and target > 0, f"🟢 {tk} hit your buy level",
                f"Consider adding to {tk} — now {cur:.2f}, your dip level {target:.2f}.", target)
    if rt == "max_weight":
        return (weight >= L, f"⚖️ {tk} is {weight:.0f}% of your portfolio",
                f"Consider trimming {tk} back toward your {L:.0f}% cap.", L)
    return (False, "", "", None)


async def evaluate_user(session: AsyncSession, user: User, *, notify: bool = False) -> list[dict]:
    """Update peaks, latch newly-triggered rules, optionally push. Returns the
    list of rules that newly triggered this run."""
    idx = await _positions_index(session, user)
    rules = (await session.scalars(
        select(TradingRule).where(TradingRule.subject == user.email,
                                  TradingRule.active.is_(True)))).all()
    newly = []
    for r in rules:
        pos = idx.get(r.ticker.upper())
        if not pos:
            continue
        cur, cost, w = pos["price"], pos["cost"], pos["weight_pct"]
        if r.rule_type == "trailing_stop" and cur > 0:
            r.peak_price = max(r.peak_price or cur, cur)
        hit, title, action, _ = _evaluate(r, cur, cost, w)
        if hit and not r.triggered:
            r.triggered = True
            r.last_triggered_at = _now()
            newly.append({"id": str(r.id), "ticker": r.ticker, "rule_type": r.rule_type,
                          "title": title, "action": action})
        elif not hit and r.triggered and r.rule_type in ("price_above", "price_below"):
            r.triggered = False  # transient alerts re-arm when condition clears
    await session.commit()

    if notify and newly:
        from app.services import push_service
        for n in newly:
            await push_service.send_to_subject(
                session, user.email, n["title"], n["action"], url="/app/", tag=f"rule:{n['id']}")
    return newly


async def evaluate_all(session: AsyncSession) -> dict:
    """Evaluate every subject that has rules (used by the scheduled price job)."""
    from app.services.feed_service import ensure_user
    subjects = list((await session.scalars(
        select(TradingRule.subject).where(TradingRule.active.is_(True)).distinct())).all())
    total = 0
    for subj in subjects:
        user = await ensure_user(session, subj)
        await session.flush()
        total += len(await evaluate_user(session, user, notify=True))
    return {"subjects": len(subjects), "triggered": total}


# --------------------------------------------------------------------------- #
# Recommendations surfacing (triggered rules -> 'What to do now')
# --------------------------------------------------------------------------- #
async def triggered_rule_recs(session: AsyncSession, user: User) -> list[dict]:
    rules = (await session.scalars(
        select(TradingRule).where(TradingRule.subject == user.email,
                                  TradingRule.active.is_(True),
                                  TradingRule.triggered.is_(True)))).all()
    idx = await _positions_index(session, user) if rules else {}
    out = []
    for r in rules:
        pos = idx.get(r.ticker.upper()) or {"price": 0, "cost": 0, "weight_pct": 0}
        _, title, action, _t = _evaluate(r, pos["price"], pos["cost"], pos["weight_pct"])
        out.append({"id": f"rule_{str(r.id)[:8]}", "dimension": "rule",
                    "severity": _SEV.get(r.rule_type, "HIGH"),
                    "title": title or f"Rule on {r.ticker}",
                    "action": action or "Review this holding.",
                    "how": ["This is your own trading rule firing.",
                            "Place the order in your brokerage if you agree.",
                            "Delete or edit the rule in Holdings → Trading rules."]})
    return out


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
async def create_rule(session: AsyncSession, user: User, *, ticker: str, rule_type: str,
                      mode: str, level: float, note: str | None = None) -> TradingRule:
    if rule_type not in RULE_TYPES:
        raise ValueError(f"unknown rule_type '{rule_type}'")
    mode = "price" if mode == "price" else "pct"
    if rule_type in ("price_above", "price_below"):
        mode = "price"
    if rule_type in ("trailing_stop", "max_weight"):
        mode = "pct"
    rule = TradingRule(subject=user.email, ticker=ticker.strip().upper(),
                       rule_type=rule_type, mode=mode, level=float(level), note=note)
    session.add(rule)
    await session.commit()
    return rule


async def list_rules(session: AsyncSession, user: User) -> list[dict]:
    rules = (await session.scalars(
        select(TradingRule).where(TradingRule.subject == user.email)
        .order_by(TradingRule.created_at.desc()))).all()
    idx = await _positions_index(session, user) if rules else {}
    out = []
    for r in rules:
        pos = idx.get(r.ticker.upper())
        cur = pos["price"] if pos else None
        _, _title, _action, target = _evaluate(
            r, pos["price"], pos["cost"], pos["weight_pct"]) if pos else (False, "", "", None)
        out.append({"id": str(r.id), "ticker": r.ticker, "rule_type": r.rule_type,
                    "mode": r.mode, "level": r.level, "note": r.note,
                    "active": r.active, "triggered": r.triggered,
                    "current_price": cur, "target": target,
                    "last_triggered_at": r.last_triggered_at.isoformat() if r.last_triggered_at else None})
    return out


async def suggest_rules_for_holdings(session: AsyncSession, user: User) -> list[dict]:
    """Per-holding suggested rule *set* with concrete, volatility-derived levels.

    Nothing is saved: each suggestion is a ready-to-arm rule the UI can add with
    one click (or 'add all') via the normal create-rule endpoint. Levels are
    grounded in the holding's own realized volatility, never invented; rule types
    the user has already armed are omitted so suggestions stay fresh.
    """
    from app.providers.registry import guarded_history
    from app.services.recommendations import stop_buffer_pct

    idx = await _positions_index(session, user)
    if not idx:
        return []
    existing = (await session.scalars(
        select(TradingRule).where(TradingRule.subject == user.email,
                                  TradingRule.active.is_(True)))).all()
    have = {(r.ticker.upper(), r.rule_type) for r in existing}

    out: list[dict] = []
    for tk, pos in idx.items():
        price, cost, weight = pos["price"], pos["cost"], pos["weight_pct"]
        if price <= 0:
            continue
        try:
            hist = guarded_history(tk, days=200)
            closes = [c for _d, c in hist] if hist else []
        except Exception:  # noqa: BLE001 -- no history -> conservative fixed buffers
            closes = []
        stop_buf = stop_buffer_pct(closes, k=1.5, lo=8.0, hi=15.0)
        trail_buf = stop_buffer_pct(closes, k=2.0, lo=10.0, hi=20.0)

        cand: list[dict] = []
        stop = round(price * (1 - stop_buf / 100.0), 2)
        cand.append({"rule_type": "stop_loss", "mode": "price", "level": stop,
                     "label": f"\U0001f6d1 Stop-loss {stop:g}\u20aa",
                     "why": f"Caps downside ~{stop_buf:.0f}% below today's {price:.2f}."})
        cand.append({"rule_type": "trailing_stop", "mode": "pct", "level": trail_buf,
                     "label": f"\U0001f4c9 Trailing stop {trail_buf:g}%",
                     "why": f"Locks in gains if it falls {trail_buf:.0f}% from its peak, but lets it run."})
        if cost > 0 and price > cost:
            tp = round(price * (1 + trail_buf / 100.0), 2)
            cand.append({"rule_type": "take_profit", "mode": "price", "level": tp,
                         "label": f"\U0001f3af Take-profit {tp:g}\u20aa",
                         "why": f"Takes some off the table if it climbs ~{trail_buf:.0f}% more."})
        if weight >= 12:
            cap = float(max(15.0, 5 * math.ceil(weight / 5)))
            cand.append({"rule_type": "max_weight", "mode": "pct", "level": cap,
                         "label": f"\u2696\ufe0f Max weight {cap:g}%",
                         "why": f"It's {weight:.0f}% of your book — trim back toward {cap:.0f}% if it grows."})

        fresh = [c for c in cand if (tk, c["rule_type"]) not in have]
        for c in fresh:
            c["ticker"] = tk
        if fresh:
            out.append({"ticker": tk, "current_price": round(price, 2),
                        "weight_pct": weight, "rules": fresh})
    out.sort(key=lambda h: h["weight_pct"], reverse=True)
    return out


async def delete_rule(session: AsyncSession, user: User, rule_id: str) -> bool:
    try:
        rid = uuid.UUID(rule_id)
    except Exception:  # noqa: BLE001
        return False
    res = await session.execute(
        delete(TradingRule).where(TradingRule.id == rid, TradingRule.subject == user.email))
    await session.commit()
    return res.rowcount > 0


async def toggle_rule(session: AsyncSession, user: User, rule_id: str) -> bool:
    try:
        rid = uuid.UUID(rule_id)
    except Exception:  # noqa: BLE001
        return False
    r = await session.get(TradingRule, rid)
    if not r or r.subject != user.email:
        return False
    r.active = not r.active
    if r.active:
        r.triggered = False  # re-arm
    await session.commit()
    return True
