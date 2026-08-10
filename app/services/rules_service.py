"""Trading rules: user-defined stop-loss / take-profit / trailing-stop / price
alerts / buy-the-dip / max-weight.

A trigger drives real action in three steps, each of them recorded:
  1. ``evaluate_user`` latches the rule and writes a ``RuleEvent`` (what fired,
     at what price, against which target) — the audit trail;
  2. the event surfaces as an *executable* card carrying an ``apply`` spec, so
     Accept genuinely sells rather than saying "consider selling";
  3. executing (or ignoring) stamps the outcome back onto that same event.

InvestWise still places no broker orders — executing updates the tracked book
and tells you so plainly, so you can mirror the trade at your broker.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import RuleEvent, TradingRule, User
from app.services.portfolio_analytics import compute_snapshot, load_positions

RULE_TYPES = {"stop_loss", "take_profit", "trailing_stop", "price_above",
              "price_below", "buy_dip", "max_weight", "strategy_signal"}

# An entry/exit rule is a SUBSCRIPTION to one strategy's own signal, not a
# reimplementation of it.
#
# The alternative was discrete indicator rules -- ma_above, rsi_below,
# breakout_high -- which would mean a second copy of SMA/RSI/Donchian living
# here alongside the backtest engine's. Two implementations of one job is what
# produced the duplicated reprice loop (which corrupted a live cash row) and the
# futures-vs-proxy regime split. It would also make the promised per-rule
# statistics describe something that was never measured.
#
# So these rules fire off the flip `strategy_signal_service` already records,
# which comes from the same `targets_for()` the backtest ran. One signal, one
# source of truth, and the rule adds what a rule is for: arm/disarm, an audit
# trail, and a notification.
_SIGNAL_MODES = ("entry", "exit")

_SEV = {"stop_loss": "CRITICAL", "trailing_stop": "CRITICAL", "max_weight": "HIGH",
        "take_profit": "HIGH", "buy_dip": "HIGH", "price_above": "MEDIUM",
        "price_below": "MEDIUM", "strategy_signal": "HIGH"}


def _now():
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Position lookup
# --------------------------------------------------------------------------- #
async def _positions_index(session: AsyncSession, user: User) -> dict[str, dict]:
    positions = await load_positions(session, user)
    snap = compute_snapshot(positions) if positions else {"nav": 0, "exposure_ticker": {}}
    weights = snap.get("exposure_ticker") or {}
    out = {}
    for p in positions:
        tk = p["ticker"].upper()
        # Cash is not a tradeable holding: a stop-loss on your bank balance is
        # meaningless. It leaked in because the index was built from every
        # position, so the rule suggester happily offered stops on CASH -- and
        # once the pricing fix reset the corrupted row from ~72.9 back to its
        # true 1.0, any such rule "crashed" and fired.
        if tk == "CASH":
            continue
        out[tk] = {"price": float(p.get("current_price") or 0),
                   "cost": float(p.get("cost_basis") or 0),
                   "qty": float(p.get("quantity") or 0),
                   "weight_pct": round((weights.get(p["ticker"], 0) or 0) * 100, 1)}
    return out


# --------------------------------------------------------------------------- #
# Execution plans — what a triggered rule would actually do
# --------------------------------------------------------------------------- #
def execution_plan(rule: TradingRule, pos: dict) -> dict | None:
    """The concrete trade a triggered rule implies, or None if it's advisory.

    Only rule types that map to an unambiguous broker order are executable: a
    stop-loss, trailing stop and take-profit are each a full exit by definition,
    and a max-weight breach has an exactly computable trim back to the cap.
    Price alerts carry no trade, and buy-the-dip needs a funding decision, so
    both stay advisory rather than inventing a size.
    """
    qty = float(pos.get("qty") or 0.0)
    if qty <= 0:
        return None
    rt = rule.rule_type
    if rt == "strategy_signal":
        # An exit is a full exit -- the strategy no longer wants the sleeve at
        # all. An entry has no unambiguous size here (that is a funding
        # decision, and funding is what "Fund this sleeve" is for), so it stays
        # advisory rather than inventing one.
        if rule.mode == "exit":
            return {"kind": "sell_position", "ticker": rule.ticker.upper(),
                    "shares": round(qty, 6), "rule_type": rt}
        return None
    if rt in ("stop_loss", "trailing_stop", "take_profit"):
        return {"kind": "sell_position", "ticker": rule.ticker.upper(),
                "shares": round(qty, 6), "rule_type": rt}
    if rt == "max_weight":
        weight = float(pos.get("weight_pct") or 0.0)
        if weight <= float(rule.level) or weight <= 0:
            return None
        shares = round(qty * (weight - float(rule.level)) / weight, 6)
        if shares <= 0:
            return None
        return {"kind": "trim", "ticker": rule.ticker.upper(),
                "shares": shares, "rule_type": rt}
    return None


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


async def _signal_flip(session: AsyncSession, user: User, strategy_id: str) -> dict | None:
    """The flip `strategy_signal_service` has already recorded, if one is open.

    Read, never derive. The signal is evaluated once, by the 06:15 job, against
    the same targets_for() the backtest ran; a rule that recomputed it here would
    be a second implementation that could disagree with both.
    """
    from app.models.tables import StrategySignalState
    row = (await session.execute(
        select(StrategySignalState).where(
            StrategySignalState.subject == user.email,
            StrategySignalState.strategy_id == strategy_id))).scalar_one_or_none()
    if row is None or row.flipped_at is None:
        return None
    return {"target": row.target or {}, "previous": row.previous_target or {},
            "as_of": row.as_of}


def _signal_side(flip: dict, ticker: str) -> str | None:
    """Did the sleeve just turn ON (entry) or OFF (exit) for this ticker?"""
    tk = ticker.upper()
    now_in = float((flip.get("target") or {}).get(tk, 0.0)) > 0.005
    was_in = float((flip.get("previous") or {}).get(tk, 0.0)) > 0.005
    if now_in and not was_in:
        return "entry"
    if was_in and not now_in:
        return "exit"
    return None


async def evaluate_user(session: AsyncSession, user: User, *, notify: bool = False) -> list[dict]:
    """Update peaks, latch newly-triggered rules, optionally push. Returns the
    list of rules that newly triggered this run."""
    idx = await _positions_index(session, user)
    rules = (await session.scalars(
        select(TradingRule).where(TradingRule.subject == user.email,
                                  TradingRule.active.is_(True)))).all()
    newly = []
    _flips: dict[str, dict | None] = {}
    for r in rules:
        if r.rule_type == "strategy_signal":
            sid = r.strategy_id
            if not sid:
                continue
            if sid not in _flips:
                _flips[sid] = await _signal_flip(session, user, sid)
            flip = _flips[sid]
            side = _signal_side(flip, r.ticker) if flip else None
            if side == r.mode and not r.triggered:
                r.triggered = True
                r.last_triggered_at = _now()
                pos = idx.get(r.ticker.upper()) or {"price": 0, "cost": 0,
                                                    "qty": 0, "weight_pct": 0}
                verb = "wants in" if side == "entry" else "wants out"
                title = f"\U0001f4c8 {r.ticker} {verb}" if side == "entry" else f"\U0001f4c9 {r.ticker} {verb}"
                action = (f"Your {sid} rule flipped to {side}: it now wants "
                          f"{', '.join(f'{k} {round(v * 100)}%' for k, v in sorted((flip.get('target') or {}).items())) or 'no position'}.")
                event = RuleEvent(
                    subject=user.email, rule_id=str(r.id), ticker=r.ticker,
                    rule_type=r.rule_type, trigger_price=pos["price"],
                    target_price=None, title=title, outcome="triggered",
                    action={"plan": execution_plan(r, pos) or {}, "side": side,
                            "strategy_id": sid},
                )
                session.add(event)
                newly.append({"id": str(r.id), "ticker": r.ticker,
                              "rule_type": r.rule_type, "title": title,
                              "action": action, "event": event})
            continue
        pos = idx.get(r.ticker.upper())
        if not pos:
            # The thing this rule watches is no longer a tradeable holding —
            # sold, or (for CASH) never one to begin with. Retire it instead of
            # skipping, which would leave a latched `triggered` flag counting
            # toward the banner forever with no card able to clear it.
            if r.triggered or r.ticker.upper() == "CASH":
                r.triggered = False
                r.active = False
            continue
        cur, cost, w = pos["price"], pos["cost"], pos["weight_pct"]
        if r.rule_type == "trailing_stop" and cur > 0:
            r.peak_price = max(r.peak_price or cur, cur)
        hit, title, action, target = _evaluate(r, cur, cost, w)
        if hit and not r.triggered:
            r.triggered = True
            r.last_triggered_at = _now()
            # One row per firing: this is the record that survives the card being
            # accepted, ignored or regenerated.
            event = RuleEvent(
                subject=user.email, rule_id=str(r.id), ticker=r.ticker,
                rule_type=r.rule_type, trigger_price=cur, target_price=target,
                title=title, outcome="triggered",
                action={"plan": execution_plan(r, pos) or {}},
            )
            session.add(event)
            newly.append({"id": str(r.id), "ticker": r.ticker, "rule_type": r.rule_type,
                          "title": title, "action": action, "event": event})
        elif not hit and r.triggered and r.rule_type in ("price_above", "price_below",
                                                         "max_weight"):
            # Standing conditions re-arm the moment they stop being true.
            #
            # max_weight was missing here, and it is the one that matters most:
            # a cap fires at 10.2%, the position drifts back to 9.7% on its own,
            # and Today keeps saying "SOXL is 10% of your portfolio - consider
            # trimming" about a breach that has already corrected itself. Worse,
            # `execution_plan` correctly returns None once the weight is back
            # under the cap, so the card degrades to guidance with no action --
            # a nag the user cannot clear by doing anything.
            #
            # Seen live on SOXL at 9.66% against a 10% cap.
            r.triggered = False
    await session.commit()

    if notify and newly:
        from app.services import push_service
        for n in newly:
            sent = await push_service.send_to_subject(
                session, user.email, n["title"], n["action"], url="/app/", tag=f"rule:{n['id']}")
            n["event"].notified = bool(sent)
        await session.commit()
    for n in newly:
        n["event_id"] = str(n["event"].id)
        n.pop("event", None)
    return newly


# --------------------------------------------------------------------------- #
# Event log (audit trail)
# --------------------------------------------------------------------------- #
async def list_events(session: AsyncSession, user: User, limit: int = 50) -> list[dict]:
    """Most-recent rule firings and what was done about each one."""
    rows = (await session.scalars(
        select(RuleEvent).where(RuleEvent.subject == user.email)
        .order_by(RuleEvent.created_at.desc()).limit(limit))).all()
    return [{
        "id": str(e.id), "rule_id": e.rule_id, "ticker": e.ticker,
        "rule_type": e.rule_type, "title": e.title,
        "trigger_price": e.trigger_price, "target_price": e.target_price,
        "outcome": e.outcome, "notified": e.notified,
        "triggered_at": e.created_at.isoformat() if e.created_at else None,
        "outcome_at": e.outcome_at.isoformat() if e.outcome_at else None,
        "action": e.action or {},
    } for e in rows]


# A fired stop-loss / take-profit / trailing stop / buy-dip is a ONE-SHOT order:
# once you've dealt with it, it's spent. A max-weight cap or a price alert is a
# standing condition, so it re-arms instead.
_ONE_SHOT = {"stop_loss", "take_profit", "trailing_stop", "buy_dip"}


async def resolve_rule(session: AsyncSession, user: User, rule_id_or_prefix: str,
                       outcome: str, action: dict | None = None) -> bool:
    """Close the loop on a triggered rule once the user has dealt with it.

    Without this the banner never cleared: `triggered` latches True and only
    price alerts ever reset it, so "4 trading rules triggered" persisted forever
    even after the user had acted — nagging about work already done.
    """
    rid = str(rule_id_or_prefix)
    rule = None
    try:
        rule = await session.get(TradingRule, uuid.UUID(rid))
    except Exception:  # noqa: BLE001 -- not a full UUID; fall back to a prefix match
        rule = None
    if rule is None:
        for r in (await session.scalars(
                select(TradingRule).where(TradingRule.subject == user.email))).all():
            if str(r.id).startswith(rid):
                rule = r
                break
    if rule is None or rule.subject != user.email:
        return False

    await record_outcome(session, user, str(rule.id), outcome, action)
    rule.triggered = False
    if rule.rule_type in _ONE_SHOT:
        # Spent: the exit it described has either happened or been declined.
        # Leaving it armed would re-fire on the same condition immediately.
        rule.active = False
    await session.commit()
    return True


async def record_outcome(session: AsyncSession, user: User, rule_id: str,
                         outcome: str, action: dict | None = None) -> bool:
    """Stamp the newest open event for a rule with what the user did.

    Only the still-open ("triggered") event is updated, so re-accepting a
    regenerated card can never rewrite the history of an earlier firing.
    """
    event = await session.scalar(
        select(RuleEvent).where(RuleEvent.subject == user.email,
                                RuleEvent.rule_id == str(rule_id),
                                RuleEvent.outcome == "triggered")
        .order_by(RuleEvent.created_at.desc()).limit(1))
    if event is None:
        return False
    event.outcome = outcome
    event.outcome_at = _now()
    if action:
        event.action = {**(event.action or {}), "executed": action}
    await session.commit()
    return True


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
    _cleared = False
    for r in rules:
        pos = idx.get(r.ticker.upper()) or {"price": 0, "cost": 0, "qty": 0, "weight_pct": 0}
        hit, title, action, _t = _evaluate(r, pos["price"], pos["cost"], pos["weight_pct"])

        # A standing condition that is no longer true must not still have a card.
        #
        # This discarded `hit` and rendered a card purely from the latched flag,
        # so a cap that fired at 10.2% and drifted back to 9.7% kept telling the
        # user to "consider trimming SOXL back toward your 10% cap" -- while
        # execution_plan correctly returned None, leaving guidance with nothing
        # to press. Clearing it in `evaluate_user` alone was not enough: that
        # runs in the 30-minute job, so Today stayed wrong in between.
        #
        # Cleared here too, where the index is already loaded, so the screen is
        # self-consistent the moment it renders rather than eventually.
        if not hit and r.rule_type in ("price_above", "price_below", "max_weight"):
            r.triggered = False
            _cleared = True
            continue
        plan = execution_plan(r, pos)
        price = float(pos.get("price") or 0)
        if plan:
            shares, value = plan["shares"], plan["shares"] * price
            verb = "Sell all" if plan["kind"] == "sell_position" else "Trim"
            action = (f"{verb} {shares:g} {r.ticker} at {price:,.2f} "
                      f"(~{value:,.0f}) — the rule you set.")
            how = [f"Accept executes this: {verb.lower()} {shares:g} {r.ticker} in your "
                   "InvestWise book and credits the net-of-tax proceeds to cash.",
                   "No brokerage order is placed — mirror the trade at your broker.",
                   "Ignore leaves the position alone and logs that you declined."]
        else:
            how = ["This is your own trading rule firing.",
                   "There's no single obvious trade here, so the app won't guess one.",
                   "Place the order in your brokerage if you agree."]
        out.append({"id": f"rule_{str(r.id)[:8]}", "dimension": "rule",
                    "severity": _SEV.get(r.rule_type, "HIGH"),
                    "title": title or f"Rule on {r.ticker}",
                    "action": action or "Review this holding.",
                    "rule_id": str(r.id),
                    "apply": {**(plan or {"kind": "none"}), "rule_id": str(r.id)},
                    "how": how})
    if _cleared:
        # Persist the re-arm, or the next render latches it all over again and
        # the card flickers back on every second load.
        await session.commit()
    return out


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
async def create_rule(session: AsyncSession, user: User, *, ticker: str, rule_type: str,
                      mode: str, level: float, note: str | None = None,
                      strategy_id: str | None = None) -> TradingRule:
    if rule_type not in RULE_TYPES:
        raise ValueError(f"unknown rule_type '{rule_type}'")
    if rule_type == "strategy_signal":
        # Pinned at arm time. An unpinned rule would follow whatever strategy
        # happened to be applied later, which is not what anyone armed.
        if not strategy_id:
            raise ValueError("a strategy_signal rule must name the strategy it follows")
        if mode not in _SIGNAL_MODES:
            raise ValueError(f"strategy_signal mode must be one of {_SIGNAL_MODES}")
        level = 0.0          # the signal is the trigger; there is no level
    else:
        mode = "price" if mode == "price" else "pct"
        if rule_type in ("price_above", "price_below"):
            mode = "price"
        if rule_type in ("trailing_stop", "max_weight"):
            mode = "pct"
    rule = TradingRule(subject=user.email, ticker=ticker.strip().upper(),
                       rule_type=rule_type, mode=mode, level=float(level), note=note,
                       strategy_id=strategy_id)
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
        hit, _title, _action, target = _evaluate(
            r, pos["price"], pos["cost"], pos["weight_pct"]) if pos else (False, "", "", None)

        # Report the quantity the rule ACTUALLY watches, and its unit.
        #
        # This returned `current_price` for every rule type, so a max_weight rule
        # rendered as "now 137.61 -> 10.00": SOXL's dollar price against a
        # percent-of-portfolio cap. Two different quantities on one row, and it
        # reads as a thirteenfold breach when the position is at 9.7% of a 10%
        # cap. The number was never wrong, it was never comparable.
        if r.rule_type == "max_weight":
            current, unit = ((pos["weight_pct"] if pos else None), "pct_of_portfolio")
        elif r.rule_type == "strategy_signal":
            current, unit = (None, "signal")
        else:
            current, unit = (cur, "price")

        out.append({"id": str(r.id), "ticker": r.ticker, "rule_type": r.rule_type,
                    "mode": r.mode, "level": r.level, "note": r.note,
                    "strategy_id": r.strategy_id,
                    "active": r.active, "triggered": r.triggered,
                    "current_price": cur,
                    "current": current, "unit": unit,
                    # `triggered` is a LATCH -- it stays set until the user deals
                    # with it. `breached_now` is whether the condition is true
                    # this second. A cap that fired at 10.2% and drifted back to
                    # 9.7% is triggered=True, breached_now=False, and showing only
                    # the first makes the app look like it is nagging about
                    # nothing.
                    "breached_now": bool(hit),
                    "target": target,
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
