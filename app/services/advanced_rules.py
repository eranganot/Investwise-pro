"""PR 81 -- four advanced trading rules, and the cards only they can produce.

Eran, 2026-09-29: "add a few more trading rules (more advanced ones)". He chose
all four in PLAN_2026-09-29_advanced_rules.md and decided:

  1. a second setting lives in `trading_rules.params` (migration 0025, PR 80);
  2. a ONE-TIME card offers to convert max-weight caps into rebalance bands;
  3. the drawdown guard alerts AND proposes an actionable de-risk;
  4. the defaults stand -- band 3 pts, profit-protect activation +20%, the
     200-day trend, 10% drawdown -- "BUT if they need to be changed a
     recommendation card should appear" (`tune_cards`).

    rebalance_band  level = band width in points. The target is read LIVE from
                    the plan (`plan_ticker_targets`), never stored, so the band
                    follows the plan and cannot fight it (the PR 79 VTI loop).
                    Above the band: a trim BACK TO THE TARGET. Below: a funded
                    buy through `propose_funded_buy` (CLAUDE.md: the only way).
    profit_protect  level = trail %. params.activation_pct (default 20). Dormant
                    until price >= cost x (1 + activation); from then it tracks
                    the peak and exits at peak x (1 - trail).
    trend_break     level = 200 (the 50/200-day cross, "long") or 50 (20/50,
                    "short") -- the SAME two signals the holding review
                    backtests (PR 77), so "is selling on this signal any good"
                    has a measured answer. A sell only with a fresh measured
                    edge; otherwise the card arms a volatility-sized stop.
    drawdown_guard  level = % below the book's peak, flow-adjusted (a
                    withdrawal is not a loss). Ticker `__BOOK__`.

Every evaluation here is PURE over a `pos` dict that `augment` has already
filled, so the 30-minute job never fetches: trend metrics are stored nightly by
the holding review, targets come from the plan, drawdown from the snapshots.
"""
from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

BOOK = "__BOOK__"
ADV_TYPES = {"rebalance_band", "profit_protect", "trend_break", "drawdown_guard",
             # PR 83: a buy-back hold, as a rule you can see and edit
             "buy_back"}
# Protective exits: never negotiated against a plan target (PR 79 pass) and
# never suppressed by the sleeve-at-target contradiction rule.
PROTECTIVE = {"stop_loss", "trailing_stop", "profit_protect", "trend_break", "drawdown_guard"}
STANDING = {"rebalance_band", "trend_break", "drawdown_guard"}   # re-arm when cleared

DEFAULTS = {"rebalance_band": 3.0, "profit_protect": 15.0, "trend_break": 200.0,
            "drawdown_guard": 10.0, "activation_pct": 20.0}
# What a de-risk sells of the most volatile holding: the house slice
# (rules_service.TAKE_PROFIT_FRACTION is the same 25%).
DERISK_FRACTION = 0.25
# The guard re-arms once the drawdown is back under max(level / 2, level - this).
REARM_GAP_PTS = 2.5


def derisk_fraction() -> float:
    """PR 121: what the guard sells is a setting (default 25%) the retune can move."""
    from app.services import thresholds
    return float(thresholds.get("guard_sell_fraction_pct", DERISK_FRACTION * 100.0)) / 100.0


def rearm_gap_pts() -> float:
    """PR 121: how far under its level the guard re-arms (default 2.5 points)."""
    from app.services import thresholds
    return float(thresholds.get("guard_rearm_gap_pts", REARM_GAP_PTS))


def guard_rearm_pct(level: float) -> float:
    """The drawdown (%) a fired guard must be back under to re-arm."""
    return max(level / 2.0, level - rearm_gap_pts())


def _band_default() -> float:
    """PR 104d: the rebalance band is a setting (plans.thresholds); 3 points by default."""
    from app.services import thresholds
    return float(thresholds.get("rebalance_band_pts", DEFAULTS["rebalance_band"]))

_SIG_WORDS = {"long": "50-day average fell below its 200-day",
              "short": "20-day average fell below its 50-day"}


def signal_of(rule) -> str:
    return "short" if float(getattr(rule, "level", 200) or 200) < 100 else "long"


def params_of(rule) -> dict:
    p = getattr(rule, "params", None)
    return dict(p) if isinstance(p, dict) else {}


def _min_trade() -> float:
    from app.services.funding_service import MIN_TRADE_ILS
    return float(MIN_TRADE_ILS)


def validate(rule_type: str, level: float | None, params: dict | None) -> tuple[float, dict | None]:
    """Clamp an advanced rule's settings to what it can mean. Raises ValueError."""
    lvl = float(level if level is not None else DEFAULTS.get(rule_type, 0) or 0)
    p = dict(params or {})
    if rule_type == "rebalance_band":
        if not 0.5 <= lvl <= 25:
            raise ValueError("a band is between 0.5 and 25 points either side of the target")
    elif rule_type == "profit_protect":
        if not 2 <= lvl <= 50:
            raise ValueError("a profit-protect trail is between 2% and 50%")
        act = float(p.get("activation_pct", DEFAULTS["activation_pct"]))
        if not 1 <= act <= 500:
            raise ValueError("activation is a gain between +1% and +500%")
        p = {k: v for k, v in p.items() if k in ("activation_pct",)}
        p["activation_pct"] = act
    elif rule_type == "trend_break":
        if lvl not in (50.0, 200.0):
            raise ValueError("a trend break watches the 50-day (20/50 cross) or 200-day (50/200 cross)")
        p = {}
    elif rule_type == "drawdown_guard":
        if not 2 <= lvl <= 60:
            raise ValueError("a drawdown guard is between 2% and 60%")
        p = {}
    elif rule_type == "buy_back":
        if lvl < 0:
            raise ValueError("a buy-back level is a price, zero or above")
        return lvl, (p or None)
    return lvl, (p or None)


# --------------------------------------------------------------------------- #
# Pure evaluation
# --------------------------------------------------------------------------- #
def band_side(rule, pos: dict) -> tuple[str | None, float, float]:
    """(\"above\" | \"below\" | None, target %, trade value ILS)."""
    t = pos.get("plan_target_pct")
    if t is None:
        return None, 0.0, 0.0
    w = float(pos.get("weight_pct") or 0.0)
    band = abs(float(rule.level or _band_default()))
    dev = w - float(t)
    nav = float(pos.get("nav") or 0.0)
    trade = abs(dev) / 100.0 * nav
    if abs(dev) <= band or trade < _min_trade():
        return None, float(t), trade
    return ("above" if dev > 0 else "below"), float(t), trade


def evaluate(rule, pos: dict) -> tuple[bool, str, str, float | None]:
    """(hit, title, action, target) for the four advanced types."""
    rt, tk, L = rule.rule_type, rule.ticker, float(rule.level or 0)
    cur = float(pos.get("price") or 0.0)
    if rt == "rebalance_band":
        side, t, trade = band_side(rule, pos)
        w = float(pos.get("weight_pct") or 0.0)
        if side is None:
            return False, "", "", (t or None)
        return (True, f"⚖️ {tk} is {w:.1f}% — {side} your {t:.1f}% ±{L:g} band",
                (f"{'Trim' if side == 'above' else 'Top up'} {tk} back to your plan's "
                 f"{t:.1f}% (~₪{trade:,.0f})."), t)
    if rt == "profit_protect":
        p = params_of(rule)
        act = float(p.get("activation_pct") or DEFAULTS["activation_pct"])
        cost = float(pos.get("cost") or 0.0)
        if not p.get("activated"):
            return False, "", "", (cost * (1 + act / 100.0) if cost else None)
        peak = float(getattr(rule, "peak_price", None) or cur)
        stop = peak * (1 - abs(L) / 100.0)
        return (cur > 0 and cur <= stop,
                f"🛡️ {tk} gave back {abs(L):g}% from its {peak:,.2f} high",
                (f"Your profit-protect armed at +{act:g}% and {tk} has fallen to {cur:,.2f} "
                 f"(stop {stop:,.2f}). Sell to keep the gain."), stop)
    if rt == "trend_break":
        sig = signal_of(rule)
        tr = pos.get("trend") or {}
        m = tr.get("metrics") or {}
        if not tr.get("fresh") or not m.get("ok"):
            return False, "", "", None
        return (m.get(sig) == "down", f"📉 {tk}: the {_SIG_WORDS[sig]}",
                f"Your trend-break rule on {tk} fired: the {_SIG_WORDS[sig]}.", None)
    if rt == "buy_back":
        # Level 0 = "until the drawdown guard re-arms": never a price event.
        # PR 139: nor is a stop hold -- a lower price is the market the stop
        # sold out of, not a reason to buy it back.
        _bp = rule.params if isinstance(rule.params, dict) else {}
        if _bp.get("kind") == "stop" or L <= 0 or cur <= 0:
            return False, "", "", (L or None)
        return (cur < L, f"↩️ {tk} fell below your buy-back level {L:,.2f}",
                (f"{tk} is {cur:,.2f}, under the {L:,.2f} you set when you sold. The cash "
                 f"held for it is free — buy it back?"), L)
    if rt == "drawdown_guard":
        b = pos.get("book") or {}
        dd = b.get("dd_pct")
        if dd is None:
            return False, "", "", None
        return (dd >= L, f"🧯 Your book is {dd:.1f}% below its peak",
                (f"Your drawdown guard is set at {L:g}%: the book is {dd:.1f}% below its "
                 f"flow-adjusted peak."), L)
    return False, "", "", None


def clears(rule, pos: dict) -> bool:
    """A standing rule re-arms only once the condition has clearly gone away."""
    rt, L = rule.rule_type, float(rule.level or 0)
    if rt == "rebalance_band":
        t = pos.get("plan_target_pct")
        return t is not None and abs(float(pos.get("weight_pct") or 0) - float(t)) <= max(0.5, L - 0.5)
    if rt == "trend_break":
        m = ((pos.get("trend") or {}).get("metrics")) or {}
        return m.get(signal_of(rule)) == "up"
    if rt == "drawdown_guard":
        dd = (pos.get("book") or {}).get("dd_pct")
        return dd is not None and dd <= guard_rearm_pct(L)
    return False


def execution_plan(rule, pos: dict) -> dict | None:
    """The trade a firing implies -- or None when the card is built elsewhere
    (a band BUY needs funding; a drawdown picks what to sell; a trend without a
    measured edge arms a stop instead)."""
    qty = float(pos.get("qty") or 0.0)
    rt, tk = rule.rule_type, rule.ticker.upper()
    if rt == "rebalance_band":
        side, t, trade = band_side(rule, pos)
        w = float(pos.get("weight_pct") or 0.0)
        if side != "above" or qty <= 0 or w <= 0:
            return None
        shares = round(qty * (w - t) / w, 6)
        return ({"kind": "trim", "ticker": tk, "shares": shares, "rule_type": rt}
                if shares > 0 else None)
    if rt == "profit_protect":
        return ({"kind": "sell_position", "ticker": tk, "shares": round(qty, 6),
                 "rule_type": rt} if qty > 0 else None)
    if rt == "trend_break":
        tr = pos.get("trend") or {}
        j = tr.get(signal_of(rule)) or {}
        if not (tr.get("fresh") and j.get("edge")) or qty <= 0:
            return None
        if signal_of(rule) == "long":
            return {"kind": "sell_position", "ticker": tk, "shares": round(qty, 6),
                    "rule_type": rt}
        from app.services.holding_review import TRIM_FRACTION
        return {"kind": "trim", "ticker": tk, "rule_type": rt, "hold_after_sale": True,
                "shares": round(qty * float(TRIM_FRACTION["value"]), 6)}
    return None


# --------------------------------------------------------------------------- #
# Context: what the pure half reads
# --------------------------------------------------------------------------- #
def drawdown_from(points: list[dict], flows: list[dict], nav_now: float | None,
                  traded=None) -> dict | None:
    """Flow-adjusted drawdown from the daily snapshots (`time_weighted`), with
    today's live NAV appended as one more step when it is given."""
    from app.services.nav_history import time_weighted
    pts = list(points or [])
    if nav_now and pts:
        from datetime import date
        today = date.today().isoformat()
        if pts[-1]["as_of"] < today:
            pts.append({"as_of": today, "nav_ils": float(nav_now),
                        "invested_ils": pts[-1].get("invested_ils", 0.0),
                        "positions": pts[-1].get("positions", 0)})
        else:
            pts[-1] = {**pts[-1], "nav_ils": float(nav_now)}
    if len(pts) < 2:
        return None
    # PR 82: WITH THE TRADE LEDGER, exactly as the NAV chart (`series_for`) calls
    # it. Without `traded`, every day the holding COUNT changed was "unmeasured"
    # -- 13 days on the live book, including the whole 09-17 -> 09-22 SOXL rally
    # -- and dropping them kept the falls and lost the rises: 13.3% "drawdown"
    # against a real 5.26%. And the Nones must be dropped as (date, pct) PAIRS:
    # filtering pct alone shifted every later date (the peak read "09-03").
    tw = time_weighted(pts, flows, traded=traded)
    pairs = [(d, p) for d, p in zip(tw.get("dates") or [], tw.get("pct") or [])
             if p is not None]
    if len(pairs) < 2:
        return None
    idx = [1.0 + p / 100.0 for _d, p in pairs]
    peak_i = max(range(len(idx)), key=lambda i: idx[i])
    dd = max(0.0, (1.0 - idx[-1] / idx[peak_i]) * 100.0)
    return {"dd_pct": round(dd, 2), "peak_on": pairs[peak_i][0], "points": len(idx),
            "unmeasured_days": len(tw.get("unmeasured_days") or [])}


async def book_drawdown(session, user, nav_now: float | None = None) -> dict | None:
    from sqlalchemy import select

    from app.models.tables import NavSnapshot
    from app.services.intake_service import list_contributions
    rows = (await session.execute(
        select(NavSnapshot).where(NavSnapshot.subject == user.email)
        .order_by(NavSnapshot.as_of))).scalars().all()
    points = [{"as_of": r.as_of, "nav_ils": float(r.nav_ils or 0.0),
               "invested_ils": float(r.invested_ils or 0.0),
               "positions": int(r.positions or 0)} for r in rows]
    flows = await list_contributions(session, user)
    from app.services.ledger_service import traded_dates
    _traded = await traded_dates(session, user)

    def traded(after: str, upto: str) -> bool:
        # Half-open, matching `nav_history.series_for` and `net_flow_between`.
        return any(after < d <= upto for d in _traded)
    return drawdown_from(points, flows, nav_now, traded=traded)


async def augment(session, user, idx: dict, nav: float, rules) -> dict:
    """Fill each position with what the advanced rules read. Returns `idx`
    (mutated) -- plus a `__BOOK__` entry when a drawdown guard exists."""
    types = {r.rule_type for r in rules if getattr(r, "active", True)}
    if not (types & ADV_TYPES):
        return idx
    for p in idx.values():
        p.setdefault("nav", nav)
    if types & {"rebalance_band", "buy_back"}:
        try:
            from app.services.recommendations import plan_ticker_targets
            tg = await plan_ticker_targets(session, user)
            for tk, p in idx.items():
                t = tg.get(tk.upper())
                p["plan_target_pct"] = round(t * 100.0, 3) if t else None
        except Exception:  # noqa: BLE001 - a missing target means "no band yet"
            logger.warning("band targets unavailable", exc_info=True)
    if "trend_break" in types:
        try:
            from app.services import holding_review as hr
            tks = sorted({r.ticker.upper() for r in rules if r.rule_type == "trend_break"})
            got = await hr.stored(session, tks)
            for tk in tks:
                meas = got.get(tk)
                if tk in idx:
                    idx[tk]["trend"] = {**(meas or {}), "fresh": hr.fresh(meas)[0]}
        except Exception:  # noqa: BLE001
            logger.warning("trend measurements unavailable", exc_info=True)
    if "drawdown_guard" in types:
        try:
            book = await book_drawdown(session, user, nav_now=nav)
        except Exception:  # noqa: BLE001
            logger.warning("book drawdown unavailable", exc_info=True)
            book = None
        idx[BOOK] = {"price": float(nav or 0.0), "cost": 0.0, "qty": 0.0,
                     "weight_pct": 100.0, "value_ils": float(nav or 0.0), "nav": nav,
                     "book": book or {}, "lots": []}
    return idx


# --------------------------------------------------------------------------- #
# Cards the generic rule card cannot make
# --------------------------------------------------------------------------- #
def _ils(x: float) -> str:
    return f"₪{float(x):,.0f}"


def _rid(*parts) -> str:
    import hashlib
    return "rec_" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:6]


def most_volatile(idx: dict, exclude: set[str]) -> tuple[str, float] | None:
    """The holding with the highest stored 20-day volatility; bonds and cash out.
    Falls back to the largest equity weight when nothing is measured."""
    best, best_v = None, -1.0
    for tk, p in idx.items():
        if tk == BOOK or tk in exclude or float(p.get("qty") or 0) <= 0:
            continue
        from app.services.allocation_mix import classify
        cls = classify(tk, p.get("market") or "", (p.get("meta") or {}).get("asset_class"))
        if cls in ("Fixed Income", "Cash"):
            continue
        v = ((p.get("trend") or {}).get("metrics") or {}).get("vol_20d_pct")
        score = float(v) if v is not None else float(p.get("weight_pct") or 0) / 1000.0
        if score > best_v:
            best, best_v = tk, score
    return (best, best_v) if best else None


async def extra_cards(session, user, *, rows, snap, plan, objective, cap, cash,
                      book_target, name_caps, ledger) -> list[dict]:
    """Band buys, drawdown de-risks, trend stops, tuning suggestions and the
    one-time caps-to-bands card. Never raises."""
    from sqlalchemy import select

    from app.models.tables import TradingRule
    from app.services import rules_service as rs
    out: list[dict] = []
    try:
        rules = (await session.scalars(select(TradingRule).where(
            TradingRule.subject == user.email, TradingRule.active.is_(True)))).all()
        idx, nav = await rs._positions_index_and_nav(session, user)
        await augment(session, user, idx, nav, rules)
        # measured volatility for the de-risk pick and the tuning checks
        try:
            from app.services import holding_review as hr
            got = await hr.stored(session, sorted(t for t in idx if t != BOOK))
            for tk, meas in got.items():
                if tk in idx and "trend" not in idx[tk]:
                    idx[tk]["trend"] = {**meas, "fresh": hr.fresh(meas)[0]}
        except Exception:  # noqa: BLE001
            pass
        for r in rules:
            pos = idx.get(r.ticker.upper())
            if not pos:
                continue
            try:
                if r.triggered and r.rule_type == "rebalance_band":
                    c = _band_buy_card(r, pos, rows=rows, snap=snap, plan=plan,
                                       objective=objective, cap=cap, cash=cash,
                                       book_target=book_target, name_caps=name_caps,
                                       ledger=ledger)
                    if c:
                        out.append(c)
                elif r.triggered and r.rule_type == "drawdown_guard":
                    c = _drawdown_card(r, pos, idx, nav)
                    if c:
                        out.append(c)
                elif r.triggered and r.rule_type == "buy_back":
                    c = await _buy_back_card(session, user, r, pos, nav=nav, cash=cash,
                                             objective=objective, plan=plan)
                    if c:
                        out.append(c)
                elif r.triggered and r.rule_type == "trend_break" and not execution_plan(r, pos):
                    c = _trend_stop_card(r, pos)
                    if c:
                        out.append(c)
                t = _tune_card(r, pos)
                if t:
                    out.append(t)
            except Exception:  # noqa: BLE001
                logger.warning("advanced card failed for %s %s", r.ticker, r.rule_type,
                               exc_info=True)
        cc = await _convert_caps_card(session, user, rules, idx)
        if cc:
            out.append(cc)
    except Exception:  # noqa: BLE001
        logger.warning("advanced rule cards failed", exc_info=True)
    return out


def _band_buy_card(rule, pos, *, rows, snap, plan, objective, cap, cash, book_target,
                   name_caps, ledger) -> dict | None:
    side, t, trade = band_side(rule, pos)
    if side != "below":
        return None
    from app.services import funding_service as fs
    from app.services.allocation_mix import classify
    tk = rule.ticker.upper()
    cls = classify(tk, pos.get("market") or "", (pos.get("meta") or {}).get("asset_class"))
    buy = fs.propose_funded_buy(
        rows=rows, snap=snap, plan=plan, objective=objective, cap=cap,
        book_target=book_target, name_caps=name_caps, ticker=tk, buying_class=cls,
        cash_ils=cash, requested_ils=trade, market=pos.get("market") or "NYSE",
        allow_within_class_swap=True, ledger=ledger)
    w = float(pos.get("weight_pct") or 0.0)
    rid = f"rule_{str(rule.id)[:8]}"
    base = {"id": rid, "dimension": "rule", "severity": "HIGH", "rule_id": str(rule.id),
            "title": f"⚖️ {tk} is {w:.1f}% — below your {t:.1f}% ±{rule.level:g} band",
            "meta": {"rule_type": "rebalance_band", "ticker": tk, "triggered_at":
                     (rule.last_triggered_at.isoformat() if rule.last_triggered_at else None)}}
    if buy is None:
        return {**base, "action": (f"{tk} is below its band, but the plan has nothing over "
                                   f"target to fund it and no cash above the floor. The next "
                                   f"deposit or standing order is where it comes from."),
                "how": ["Nothing to execute today: no over-target class to sell and no spare cash",
                        "Ignore silences it until the band is breached again"],
                "apply": {"kind": "none", "rule_id": str(rule.id)}, "est_amount": None}
    return {**base,
            "action": (f"Buy {_ils(buy.amount_ils)} of {tk} to bring it back to your plan's "
                       f"{t:.1f}%. " + buy.summary),
            "impact": buy.impact,
            "how": ([f"Buy {_ils(buy.amount_ils)} of {tk}"]
                    + [f"Sell {x['shares']} {x['ticker']} (~{_ils(x['value_ils'])}) — {x['reason']}"
                       for x in buy.fund.get("sells", [])]
                    + ([f"Use {_ils(buy.fund['from_cash_ils'])} of cash"]
                       if buy.fund.get("from_cash_ils") else [])
                    + ["No brokerage order is placed — mirror it at your broker"]),
            "est_amount": buy.amount_ils,
            "apply": {**buy.apply_spec, "rule_id": str(rule.id)}}


async def _buy_back_card(session, user, rule, pos, *, nav, cash, objective, plan) -> dict | None:
    """Eran, 2026-09-29: when the price drops under the buy-back level, "push +
    buy-back card". Sized to the plan: target minus today's weight, paid from
    cash above the floor only."""
    from app.services import funding_service as fs
    from app.services.allocation_mix import classify
    from app.services.recommendations import plan_ticker_targets
    tk = rule.ticker.upper()
    t = float((await plan_ticker_targets(session, user)).get(tk) or 0.0) * 100.0
    w = float(pos.get("weight_pct") or 0.0)
    want = max(0.0, (t - w) / 100.0 * float(nav or 0.0))
    spend = fs.spendable_cash(float(cash or 0.0), float(nav or 0.0), objective, plan)
    amt = round(min(want, spend), 2)
    rid = f"rule_{str(rule.id)[:8]}"
    head = {"id": rid, "dimension": "rule", "severity": "HIGH", "rule_id": str(rule.id),
            "title": f"↩️ {tk} is below your buy-back level {float(rule.level):,.2f}",
            "meta": {"rule_type": "buy_back", "ticker": tk}}
    if amt < fs.MIN_TRADE_ILS:
        return {**head, "action": (f"The hold on {tk} has ended, but there is no cash above your "
                                   f"cash target to buy it back with (plan wants ~₪{want:,.0f})."),
                "apply": {"kind": "none", "rule_id": str(rule.id)}, "est_amount": None}
    cls = classify(tk, pos.get("market") or "", (pos.get("meta") or {}).get("asset_class"))
    return {**head,
            "why": (f"You sold {tk} and asked the app not to buy it back until it traded below "
                    f"{float(rule.level):,.2f}. It is now {float(pos.get('price') or 0):,.2f}. "
                    f"Your plan targets {t:.1f}% in {tk}; you hold {w:.1f}%."),
            "action": f"Buy back ~₪{amt:,.0f} of {tk} from cash.",
            "how": [f"Accept buys ~₪{amt:,.0f} of {tk} at the live price from cash",
                    "The hold ends either way (Ignore ends it without buying)",
                    "No brokerage order is placed — mirror it at your broker"],
            "est_amount": amt, "actionable": True,
            "apply": {"kind": "redeploy_cash", "rule_id": str(rule.id), "legs": [
                {"ticker": tk, "amount_ils": amt, "asset_class": cls,
                 "reason": f"buy-back level {float(rule.level):,.2f} reached"}]}}


def _drawdown_card(rule, pos, idx, nav) -> dict | None:
    b = pos.get("book") or {}
    dd = b.get("dd_pct")
    if dd is None:
        return None
    pick = most_volatile(idx, exclude=set())
    rid = f"rule_{str(rule.id)[:8]}"
    head = {"id": rid, "dimension": "rule", "severity": "CRITICAL", "rule_id": str(rule.id),
            "title": f"🧯 Your book is {dd:.1f}% below its peak — de-risk?",
            "meta": {"rule_type": "drawdown_guard", "ticker": BOOK, "protective": True,
                     "triggered_at": (rule.last_triggered_at.isoformat()
                                      if rule.last_triggered_at else None)}}
    if not pick:
        return {**head, "action": "Nothing volatile enough to de-risk was found.",
                "apply": {"kind": "none", "rule_id": str(rule.id)}, "est_amount": None}
    tk, vol = pick
    p = idx[tk]
    frac = derisk_fraction()
    shares = round(float(p["qty"]) * frac, 6)
    from app.services.recommendations import _plan_sale
    pl = _plan_sale(p["lots"], shares)
    gross, tax = pl["gross"], pl["tax"]
    vol_txt = f"{vol:.0f}% annualised 20-day volatility" if vol >= 1 else "your largest equity holding"
    return {**head,
            "why": (f"Your drawdown guard is {rule.level:g}%; the book is {dd:.1f}% below its "
                    f"flow-adjusted peak (peak on {b.get('peak_on')}). {tk} is the most volatile "
                    f"thing you hold ({vol_txt})."),
            "action": (f"Sell {shares:g} {tk} ({frac:.0%}, ~{_ils(gross)}, est. tax "
                       f"{_ils(tax)}) into cash. Not bought back until the book recovers."),
            "impact": (f"{tk} goes from {p['weight_pct']:.1f}% to about "
                       f"{p['weight_pct'] * (1 - frac):.1f}% of the book; about "
                       f"{_ils(gross - tax)} lands in cash. Your plan targets are not changed, and "
                       f"re-buys of {tk} resume when the guard re-arms (drawdown back under "
                       f"{guard_rearm_pct(float(rule.level)):g}%)."),
            "how": [f"Accept sells {shares:g} {tk} in your InvestWise book and credits the net "
                    f"proceeds to cash",
                    f"A buy-back hold keeps the realign, funder and cash cards off {tk} until the "
                    f"book recovers",
                    "No brokerage order is placed — mirror the sale at your broker",
                    "Not financial advice."],
            "est_amount": round(gross, 2), "actionable": True,
            "apply": {"kind": "trim", "ticker": p["lots"][0].get("ticker") or tk,
                      "shares": shares, "rule_id": str(rule.id),
                      "hold_until_rule": str(rule.id)}}


def _trend_stop_card(rule, pos) -> dict | None:
    tr = pos.get("trend") or {}
    m = tr.get("metrics") or {}
    sig = signal_of(rule)
    j = tr.get(sig) or {}
    last = float(pos.get("price") or m.get("price") or 0)
    if last <= 0:
        return None
    vol = float(m.get("vol_20d_pct") or 0) or 20.0
    buf = max(8.0, min(15.0, 1.5 * vol * math.sqrt(10 / 252)))
    stop = round(last * (1 - buf / 100.0), 2)
    tk = rule.ticker.upper()
    why_not = (f"selling {tk} on this signal {j.get('why')}" if j.get("measured")
               else "selling on it has not been measured on this holding")
    return {"id": f"rule_{str(rule.id)[:8]}", "dimension": "rule", "severity": "HIGH",
            "rule_id": str(rule.id),
            "title": f"📉 {tk}: the {_SIG_WORDS[sig]} — protect it with a stop",
            "action": (f"Historically {why_not}, so this is not a sell. Arm a stop-loss at "
                       f"{stop:,.2f} (~{buf:.0f}% below {last:,.2f}) instead."),
            "how": [f"Accept arms a stop-loss on {tk} at {stop:,.2f}; nothing is sold now",
                    "Not financial advice."],
            "est_amount": None, "actionable": True,
            "meta": {"rule_type": "trend_break", "ticker": tk, "protective": True},
            "apply": {"kind": "create_rules", "rule_id": str(rule.id), "rules": [{
                "ticker": tk, "rule_type": "stop_loss", "mode": "price", "level": stop,
                "note": f"From your trend-break rule (~{buf:.0f}% below {last:.2f})"}]}}


def _tune_card(rule, pos) -> dict | None:
    """Eran: the defaults stand, "BUT if they need to be changed a recommendation
    card should appear". One card per rule, only when its setting is wrong for
    THIS holding, with an Accept that applies the suggested value."""
    rt, tk, L = rule.rule_type, rule.ticker.upper(), float(rule.level or 0)
    m = ((pos.get("trend") or {}).get("metrics")) or {}
    vol = m.get("vol_20d_pct")
    new, params, why = None, None, None
    if rt == "rebalance_band" and vol and pos.get("plan_target_pct"):
        t = float(pos["plan_target_pct"])
        noise = t * float(vol) / 100.0 * math.sqrt(21 / 252)   # one month of drift, in points
        if L < 0.75 * noise:
            new = float(max(3, math.ceil(noise)))
            why = (f"{tk} moves about {vol:.0f}% a year, so its weight drifts ~{noise:.1f} points "
                   f"in a normal month around your {t:.1f}% target. A ±{L:g} band would trade it "
                   f"back and forth; ±{new:g} trades only on a real move.")
        elif L > max(3.0, t / 2.0):
            new = float(max(1, round(t / 4.0)))
            why = (f"A ±{L:g} band on a {t:.1f}% target lets {tk} drift more than half its size "
                   f"before anything happens; ±{new:g} keeps it close to the plan.")
    elif rt == "profit_protect" and vol:
        buf = max(10.0, min(20.0, 2.0 * float(vol) * math.sqrt(10 / 252)))
        p = params_of(rule)
        act = float(p.get("activation_pct") or DEFAULTS["activation_pct"])
        if L < buf - 1.0:
            new = float(round(buf))
            why = (f"{tk}'s normal two-week swing is about {buf:.0f}%, so a {L:g}% trail would sell "
                   f"on noise. {new:g}% sits outside it.")
        elif act < L + 5:
            params = {"activation_pct": float(L + 5)}
            why = (f"Arming at +{act:g}% with a {L:g}% trail can lock in close to nothing; arm at "
                   f"+{L + 5:g}% so the protected gain is real.")
    elif rt == "trend_break":
        tr = pos.get("trend") or {}
        if tr.get("fresh"):
            mine, other = signal_of(rule), ("short" if signal_of(rule) == "long" else "long")
            if not (tr.get(mine) or {}).get("edge") and (tr.get(other) or {}).get("edge"):
                new = 50.0 if other == "short" else 200.0
                why = (f"On {tk}'s own history, selling on the {_SIG_WORDS[other]} beat holding and "
                       f"the signal you chose did not. Switch so a firing can be an actual sell.")
    elif rt == "drawdown_guard":
        b = pos.get("book") or {}
        if (b.get("points") or 0) >= 20 and L < 5:
            new = 10.0
            why = "A guard under 5% fires on ordinary weeks for a book with leveraged holdings."
    if new is None and params is None:
        return None
    unit = "" if rt == "trend_break" else ("%" if rt != "rebalance_band" else " pts")
    change = (f"{L:g}{unit} → {new:g}{unit}" if new is not None else
              f"activation → +{params['activation_pct']:g}%")
    return {"id": _rid("tune", str(rule.id), new, str(params)), "dimension": "rule",
            "severity": "MEDIUM",
            "title": f"Tune your {tk if tk != BOOK else 'book'} {rt.replace('_', ' ')}: {change}",
            "why": why, "action": f"Change the rule: {change}.",
            "how": ["Accept edits the rule in place; its history is kept",
                    "Ignore keeps your setting"],
            "est_amount": None, "actionable": True,
            # `target_rule_id`, NOT `rule_id`: a rule_id makes Accept RESOLVE the
            # rule (and switch a one-shot off). Tuning is not dealing with a firing.
            "apply": {"kind": "edit_rule", "target_rule_id": str(rule.id),
                      **({"level": new} if new is not None else {}),
                      **({"params": params} if params else {})},
            "meta": {"rule_type": rt, "ticker": tk}}


_CONVERTED_KEY = "caps_to_bands_done:"


async def _convert_caps_card(session, user, rules, idx) -> dict | None:
    """Decision 2: a ONE-TIME offer to turn max-weight caps on core holdings into
    bands around the plan target. Sleeve caps are left alone: the sleeve arms its
    own cap (`_arm_sleeve_caps`) and would re-create it."""
    from app.models.tables import KVSetting
    if await session.get(KVSetting, (_CONVERTED_KEY + user.email)[:80]):
        return None
    try:
        from app.services import sleeve_service as sv
        from app.services.recommendations import plan_ticker_targets
        claimed = {t.upper() for t in await sv.sleeve_tickers(session, user)}
        tg = await plan_ticker_targets(session, user)
    except Exception:  # noqa: BLE001
        return None
    banded = {r.ticker.upper() for r in rules if r.rule_type == "rebalance_band"}
    caps = [r for r in rules if r.rule_type == "max_weight" and r.ticker.upper() not in claimed
            and tg.get(r.ticker.upper()) and r.ticker.upper() not in banded]
    if not caps:
        return None
    lines = [f"{r.ticker}: cap {r.level:g}% → band {tg[r.ticker.upper()] * 100:.1f}% "
             f"±{DEFAULTS['rebalance_band']:g}" for r in caps]
    return {"id": _rid("caps_to_bands", *sorted(str(r.id) for r in caps)),
            "dimension": "rule", "severity": "MEDIUM",
            "title": f"Turn {len(caps)} max-weight cap{'s' if len(caps) > 1 else ''} into "
                     f"rebalance bands",
            "why": ("A cap only ever trims, at a number set once. A band follows your plan's "
                    "target, trims above it and tops up below it — it cannot fight the plan."),
            "action": "; ".join(lines) + ".",
            "how": ["Accept replaces each cap with a ±3-point band around the plan target",
                    "Sleeve caps are left alone (the sleeve manages its own)",
                    "This offer is shown once; Ignore keeps your caps"],
            "est_amount": None, "actionable": True,
            "apply": {"kind": "caps_to_bands", "rule_ids": [str(r.id) for r in caps],
                      "band": _band_default()}}


async def apply_caps_to_bands(session, user, spec: dict) -> dict:
    from sqlalchemy import select

    from app.models.tables import KVSetting, TradingRule
    from app.services import rules_service as rs
    ids = {str(x) for x in (spec.get("rule_ids") or [])}
    band = float(spec.get("band") or _band_default())
    done = []
    for r in (await session.scalars(select(TradingRule).where(
            TradingRule.subject == user.email))).all():
        if str(r.id) in ids and r.rule_type == "max_weight":
            tk = r.ticker
            await session.delete(r)
            await session.flush()
            await rs.create_rule(session, user, ticker=tk, rule_type="rebalance_band",
                                 mode="pct", level=band,
                                 note=f"Converted from a {r.level:g}% cap (follows your plan)")
            done.append(tk)
    session.add(KVSetting(key=(_CONVERTED_KEY + user.email)[:80], value="1"))
    await session.commit()
    return {"converted": done, "band": band}
