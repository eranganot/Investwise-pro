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

import logging
import math
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.offload import offload
from app.models.tables import RuleEvent, TradingRule, User
from app.services.portfolio_analytics import compute_snapshot, load_positions

logger = logging.getLogger(__name__)

RULE_TYPES = {"stop_loss", "take_profit", "trailing_stop", "price_above",
              "price_below", "buy_dip", "max_weight", "strategy_signal",
              # PR 81: the advanced rules (see app/services/advanced_rules.py)
              "rebalance_band", "profit_protect", "trend_break", "drawdown_guard",
              # PR 83: a buy-back hold (buyback_hold.py) -- visible and editable
              "buy_back"}

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

# Rule types where a ticker may carry AT MOST ONE active rule, so a second
# request re-levels the first instead of stacking beside it.
#
# Three independent producers emit `trailing_stop` specs -- the strategy
# discipline card, the per-holding rule suggestions, and the momentum card -- and
# none of them can see what the others are proposing on the same screen, because
# each filters only against rules already armed. Accepting two of them left MTUM
# carrying a 12% trailing stop AND a 15% one, both active, no warning. The
# tighter one fires first, so you get stopped out at 12% having decided on 15%.
#
# The codebase already reached this conclusion once for `max_weight`:
# `_arm_sleeve_caps` sums per ticker, and `strategy_catalog.discipline_rules`
# deliberately emits no max_weight because "two max_weight rules on the same
# ticker at two different levels" is a duplicate. The guard was never generalised
# to the protective stops, which have exactly the same problem.
#
# NOT included, on purpose:
#   * price_above / price_below -- "tell me at 100 and again at 120" is a
#     legitimate thing to want, and two alerts do not contradict each other.
#   * strategy_signal -- an entry and an exit rule on one ticker are both
#     wanted, and two sleeves can legitimately subscribe to the same ticker's
#     signal. Keyed below by (mode, strategy_id) rather than excluded outright.
# PR 109 (D-b, owner 2026-10-08): entry rules on a ticker you do NOT hold are
# WATCH ALERTS -- "tell me when AMZN dips to 180" -- and are never purged or
# retired for not being held. Only exit rules (stop, take-profit, trailing...)
# of a position you no longer hold are cleared, and only by the evaluator.
ENTRY_TYPES = frozenset({"buy_dip", "price_below", "price_above"})


def _watch_pos(rule) -> dict | None:
    """A watch alert's stand-in position: the last price the evaluator quoted.

    Read from the rule, never fetched, so Today stays cache-only. A watch alert
    with no quote yet has no position and cannot fire (`_evaluate` needs a price).
    """
    px = (rule.params or {}).get("watch_price") if isinstance(rule.params, dict) else None
    if not px:
        return None
    return {"price": float(px), "cost": 0.0, "qty": 0.0, "weight_pct": 0.0,
            "value_ils": 0.0, "watch": True}


def _quote_watch(ticker: str) -> float | None:
    """Live price in major units for a watched (unheld) ticker, or None. Blocking."""
    try:
        from app.providers.registry import guarded_quote
        from app.services.fx import to_major_units
        q = guarded_quote(ticker)
        px, _ccy = to_major_units(float(q.price), getattr(q, "currency", None))
        return px if px and px > 0 else None
    except Exception:  # noqa: BLE001 -- an unpriced watch alert just waits
        return None


def _is_orphan_exit(rule, held: set[str]) -> bool:
    """An EXIT rule on a ticker the book does not hold (D-b): the evaluator's to clear."""
    from app.services.advanced_rules import BOOK
    tk = (rule.ticker or "").upper()
    return (tk not in held and tk not in (BOOK, "CASH")
            and rule.rule_type not in ENTRY_TYPES)


_ONE_PER_TICKER = {"stop_loss", "take_profit", "trailing_stop", "max_weight", "buy_dip"}

_SEV = {"stop_loss": "CRITICAL", "trailing_stop": "CRITICAL", "max_weight": "HIGH",
        "take_profit": "HIGH", "buy_dip": "HIGH", "price_above": "MEDIUM",
        "price_below": "MEDIUM", "strategy_signal": "HIGH"}


# How far a standing condition must move back INSIDE its limit before the rule
# re-arms.
#
# Without a band, a position hovering at 20.2% against a 20% cap re-arms the
# moment it ticks to 19.9% and fires again on the next tick up: a notification
# every few hours about a breach worth ~43 shekels.
#
# Latching forever was the first bug; re-arming on the very next tick is its
# opposite, and both reach the user as a rule that will not stop talking. The
# band is what makes the difference -- clear on a real move, not on noise.
#
# 2.5% of the level, floored at half a point, so a 20% cap re-arms at 19.5% and
# a 500 price alert re-arms at 487.50.
def cap_trim_ils(level: float, weight_pct: float, value_ils: float) -> float:
    """What trimming back to the cap would actually sell, in shekels.

    Zero when the position is at or under its cap. One function so the firing
    test, the re-arm test and `execution_plan` cannot disagree about whether
    there is a trade behind a breach -- they did, and the disagreement is what
    put two cards on Eran's screen that could not be acted on.
    """
    lvl = abs(float(level or 0.0))
    w = float(weight_pct or 0.0)
    if w <= 0 or w <= lvl:
        return 0.0
    return max(0.0, float(value_ils or 0.0)) * ((w - lvl) / w)


def cap_breach_is_material(level: float, weight_pct: float, value_ils: float) -> bool:
    """A cap breach is a breach when there is a trade behind it.

    Eran, 2026-09-06, on two cards he could not act on: *"why do I still getting
    guidance cards? all these cards should be actionable"*. Measured on the live
    book the same minute:

        SOXL  38.002% against a 38% cap  ->  0.44 shekels to trim
        QQQ   37.004% against a 37% cap  ->  0.87 shekels to trim

    Both fired, both latched, and both rendered a card whose own `how` said
    "There's no single obvious trade here, so the app won't guess one." The
    Rules tab showed it in as many words: *"Triggered - 37.0% of book vs a 37%
    cap"*.

    **The cause is structural, not a rounding accident.** `_arm_sleeve_caps`
    arms a cap at EXACTLY the sleeve's target, `_fund_sleeve` funds the sleeve
    to EXACTLY that target, and this rule fired on `weight >= level` -- at
    equality. So every correctly funded sleeve tripped its own cap the moment it
    was funded. QQQ's card exists because PR 61 worked: the sleeve was funded to
    37% and the cap armed at 37% fired on it.

    MIN_TRADE_ILS rather than a new tolerance band, because the app already has
    a number for "not worth the friction" and it governs every other sizing
    decision here. A second threshold invented for this one rule would be a
    second answer to a question already answered. `execution_plan` has applied
    exactly this test since the MSFT case -- one layer too late, where it could
    only downgrade the card instead of preventing it.

    ``value_ils`` of 0 means the caller does not know the position's value; the
    old behaviour stands rather than silencing a rule on missing data.
    """
    if not value_ils:
        return True
    from app.services.funding_service import MIN_TRADE_ILS
    return cap_trim_ils(level, weight_pct, value_ils) >= MIN_TRADE_ILS


#: How much of a position one take-profit firing sells. A quarter: enough to be
#: worth the friction and the CGT, small enough that four rungs still leave the
#: winner running. Overridable per rule via ``meta``-free `note` parsing is
#: deliberately NOT offered -- a number hidden in prose is a number nobody can
#: audit. See `take_profit_fraction`.
TAKE_PROFIT_DEFAULT_FRACTION = 0.25


def house_take_profit_fraction() -> float:
    """PR 104d: the house slice is a setting (plans.thresholds); 25% by default."""
    from app.services import thresholds
    return float(thresholds.get("take_profit_fraction_pct", TAKE_PROFIT_DEFAULT_FRACTION * 100.0)) / 100.0


def __getattr__(name):
    if name == "TAKE_PROFIT_FRACTION":
        return house_take_profit_fraction()
    raise AttributeError(name)

#: How much higher the next rung sits after one fires. The ladder is the whole
#: point: a position that keeps trending gets sold in slices at successively
#: higher prices, which is trend-responsive in OUTCOME without predicting
#: anything, and if it rolls over instead the trailing stop takes the rest.
TAKE_PROFIT_LADDER_STEP = 0.10


def take_profit_fraction(rule) -> float:
    """The slice one firing of this take-profit sells.

    A per-rule override wins when present and sane; otherwise the house default.
    Clamped to (0, 1]: a fraction of zero is a rule that does nothing, and one
    above 1 is a full exit wearing a trim's label.
    """
    raw = getattr(rule, "trim_fraction", None)
    try:
        frac = float(raw) if raw is not None else house_take_profit_fraction()
    except (TypeError, ValueError):
        frac = house_take_profit_fraction()
    if not (0.0 < frac <= 1.0):
        frac = house_take_profit_fraction()
    return frac


def next_rung(level: float, mode: str, step: float = TAKE_PROFIT_LADDER_STEP) -> float:
    """Where a fired take-profit re-arms.

    In `price` mode the level is a price and the next rung is `step` above it.
    In `pct` mode the level is a gain percentage against cost, so the rung moves
    by `step` in percentage POINTS of that same scale -- 80 becomes 90, not 88.
    """
    lvl = float(level or 0.0)
    if str(mode) == "pct":
        return round(lvl + step * 100.0, 4)
    return round(lvl * (1.0 + step), 4)


def rearm_threshold(level: float) -> float:
    """The value a standing condition must clear to re-arm, in the rule's unit."""
    lvl = abs(float(level))
    return lvl - max(0.5, lvl * 0.025)


def clears_with_margin(rule, cur: float, weight: float, value_ils: float = 0.0) -> bool:
    """True when the condition is not merely false, but false by a margin."""
    rt = getattr(rule, "rule_type", None)
    lvl = abs(float(getattr(rule, "level", 0) or 0))
    if rt == "max_weight":
        # OR IMMATERIAL, or the two rules Eran saw could never clear. The band
        # exists to stop a position flapping across its cap; it cannot also be
        # the only way out, because a sleeve funded to exactly its own cap sits
        # 0.002 points over it and would have to fall 2.5% -- SOXL to 37.05% --
        # before the card went away. A breach with no trade behind it is not a
        # breach to begin with, so it re-arms.
        if not cap_breach_is_material(lvl, float(weight), value_ils):
            return True
        return float(weight) <= rearm_threshold(lvl)
    if rt == "price_above":
        return float(cur) <= rearm_threshold(lvl)
    if rt == "price_below":
        # Mirror image: the price must rise clearly ABOVE the alert to re-arm.
        return float(cur) >= lvl + max(0.5, lvl * 0.025)
    return True


def _now():
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Position lookup
# --------------------------------------------------------------------------- #
async def _positions_index(session: AsyncSession, user: User) -> dict[str, dict]:
    idx, _nav = await _positions_index_and_nav(session, user)
    return idx


async def _positions_index_and_nav(session: AsyncSession, user: User) -> tuple[dict, float]:
    """The index, plus the NAV it was measured against.

    Two callers need the NAV: the cap card has to say what a trim leaves cash at,
    and it may not recompute the book to find out. One snapshot, one answer.
    """
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
        # NEITHER IS A POSITION YOU NO LONGER HOLD. A row at quantity 0 is an
        # exit that failed to delete itself, and `load_positions` still returns
        # it -- so `purge_orphan_rules` saw the ticker in `held`, correctly
        # concluded its rules were not orphans, and left them armed.
        #
        # MEASURED 2026-09-14: USO at quantity 0.0 kept a take-profit at 168.17,
        # a 16% trailing stop and a stop-loss at 124.92, all `active: true`, on
        # nothing. The trim path that created the row is fixed above; this makes
        # the rules machinery immune to the next path that gets it wrong.
        if float(p.get("quantity") or 0) <= 0:
            continue
        # ONE ENTRY PER TICKER, SUMMED OVER EVERY ROW (PR 77). This kept the
        # LAST row: a ticker held 6 in one account and 4 in another read as 4
        # shares, so a stop's "Sell all" sold 4 and the rule was then consumed
        # with 6 shares left unprotected, and a take-profit sized its slice
        # from one account (found in review, reproduced). `cost` is the
        # quantity-weighted basis the thresholds compare against; `lots` are the
        # rows, so a sale is valued lot by lot (`_plan_sale`) exactly as Accept
        # executes it.
        q = float(p.get("quantity") or 0)
        e = out.get(tk)
        if e is None:
            _w = weights.get(p["ticker"], 0) or 0
            e = out[tk] = {"price": float(p.get("current_price") or 0),
                           "cost": 0.0, "qty": 0.0,
                           "weight_pct": round(_w * 100, 1),
                           # Carried so the cap card can value its own trim through
                           # the SAME `_sale_value_ils` Accept runs, rather than a
                           # second arithmetic that could disagree about FX or tax.
                           "market": p.get("market"), "meta": p.get("meta") or {},
                           # FX-normalised, from the snapshot -- price x quantity
                           # would be the position's native currency.
                           "value_ils": _w * float(snap.get("nav") or 0.0),
                           "lots": []}
        e["cost"] = ((e["cost"] * e["qty"] + float(p.get("cost_basis") or 0) * q)
                     / (e["qty"] + q))
        e["qty"] += q
        e["lots"].append(p)
    return out, float(snap.get("nav") or 0.0)


async def purge_orphan_rules(session: AsyncSession, user: User,
                             idx: dict[str, dict] | None = None) -> list[dict]:
    """DELETE rules for tickers that are not in the portfolio.

    Asked for explicitly and repeatedly: if the holding is gone, the rule goes.
    Earlier versions only retired them (active=False) and left them listed, which
    is not what "remove" means.

    A stale rule is not merely clutter. A stop-loss on AMZN at 222.58, set months
    ago against a price that has moved since, sits dormant and goes live the
    instant the position is re-bought -- firing at a level nobody chose for
    today's market. Deleting is also what makes re-buying safe: you get to set a
    fresh level instead of inheriting an old one.

    THE GUARD THAT MATTERS: nothing is deleted when the position index is empty.
    An empty book and a failed position read are indistinguishable from here, and
    wiping every rule on a transient database hiccup is unrecoverable. With no
    holdings, this does nothing at all.

    PR 109: called ONLY by the evaluator. It also ran from `list_rules`, i.e. on
    every read of Today and the Rules tab, so opening a screen deleted rules.
    The read path now HIDES orphaned exit rules instead (`_is_orphan_exit`), so
    the screen is right in between without a read writing anything. Entry rules
    on unheld tickers (D-b) are watch alerts and are never deleted here.
    """
    if idx is None:
        idx = await _positions_index(session, user)
    if not idx:
        return []
    held = set(idx)
    rules = (await session.scalars(
        select(TradingRule).where(TradingRule.subject == user.email))).all()
    # PR 109 (D-b): watch alerts on unheld tickers are kept. Measured on HEAD:
    # one GET of Today deleted AMZN buy_dip and GOOGL price_below.
    gone = [r for r in rules if _is_orphan_exit(r, held)]
    if not gone:
        return []
    removed = [{"id": str(r.id), "ticker": r.ticker, "rule_type": r.rule_type,
                "level": r.level} for r in gone]
    for r in gone:
        await session.delete(r)
    await session.commit()
    logger.info("deleted %d rule(s) for tickers no longer held: %s", len(removed),
                ", ".join(f"{r['ticker']} {r['rule_type']}" for r in removed))
    return removed


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
    from app.services import advanced_rules as _adv
    if rule.rule_type in _adv.ADV_TYPES:
        return _adv.execution_plan(rule, pos)
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
    if rt == "take_profit":
        # A TAKE-PROFIT TAKES SOME OFF THE TABLE. IT DOES NOT LIQUIDATE.
        #
        # This returned `sell_position` -- a full exit -- while the notes the
        # app writes on its own take-profit rules say, verbatim: *"Takes some
        # off the table if it climbs ~10% more."* Live on this book on
        # 2026-09-14, on QQQ at 791.02 and USO at 168.17. A card may not claim
        # a change it does not produce, and this one claimed the opposite of
        # what it did.
        #
        # WHY A FIXED SLICE AND NOT A TREND-CONDITIONED ONE. Eran asked exactly
        # that. This repo already ran the experiment: STATUS.md B2 measures the
        # trend overlay on TQQQ over 2,514 sessions at 17.19% CAGR against
        # 40.25% for holding it and 20.73% for holding QQQ -- a deeper drawdown
        # (40.55% vs 35.12%) for two thirds less return, with ~7 points a year
        # going to 45 round-trips. Conditioning exits on trend is the specific
        # thing his own data says cost him the most money.
        #
        # The trend-dependent exit already exists and is correct: `trailing_stop`
        # ratchets off `peak_price` (see `_evaluate` and the peak update in
        # `evaluate_user`), so it sells late in a strong trend and early in a
        # weak one without anyone forecasting. Two mechanical rules doing one
        # job each beats one rule guessing.
        slice_frac = take_profit_fraction(rule)
        shares = round(qty * slice_frac, 6)
        if shares <= 0:
            return None
        return {"kind": "trim", "ticker": rule.ticker.upper(),
                "shares": shares, "rule_type": rt,
                "fraction": slice_frac,
                "ladder": True}
    if rt in ("stop_loss", "trailing_stop"):
        # These two ARE full exits by definition: the thesis is broken, or the
        # gain being protected is evaporating.
        return {"kind": "sell_position", "ticker": rule.ticker.upper(),
                "shares": round(qty, 6), "rule_type": rt}
    if rt == "max_weight":
        weight = float(pos.get("weight_pct") or 0.0)
        if weight <= float(rule.level) or weight <= 0:
            return None
        over = (weight - float(rule.level)) / weight
        shares = round(qty * over, 6)
        if shares <= 0:
            return None
        # A cap has no tolerance band, so a position 0.2 points over its 20% cap
        # produces a trim worth ~43 shekels -- below MIN_TRADE_ILS, which is the
        # app's own definition of "not worth the friction". Seen live on MSFT.
        # Stay advisory rather than proposing a trade the app would refuse to
        # propose anywhere else.
        from app.services.funding_service import MIN_TRADE_ILS
        value_ils = float(pos.get("value_ils") or 0.0)
        if value_ils and value_ils * over < MIN_TRADE_ILS:
            return None
        return {"kind": "trim", "ticker": rule.ticker.upper(),
                "shares": shares, "rule_type": rt}
    return None


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
def _evaluate(rule: TradingRule, cur: float, cost: float, weight: float,
              value_ils: float = 0.0, pos: dict | None = None
              ) -> tuple[bool, str, str, float | None]:
    """Return (hit, title, action, target)."""
    rt, mode, L, tk = rule.rule_type, rule.mode, rule.level, rule.ticker
    from app.services import advanced_rules as _adv
    if rt in _adv.ADV_TYPES:
        return _adv.evaluate(rule, pos if pos is not None else {
            "price": cur, "cost": cost, "weight_pct": weight, "value_ils": value_ils})
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
        # `>=` FIRED AT EQUALITY, which is exactly where a funded sleeve sits.
        # The materiality test subsumes that and the near-equality case with it:
        # see `cap_breach_is_material`.
        return (weight >= L and cap_breach_is_material(L, weight, value_ils),
                f"⚖️ {tk} is {weight:.0f}% of your portfolio",
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


async def heal_dismissed_rules(session: AsyncSession, user: User) -> list[str]:
    """Resolve a triggered rule whose card you dismissed or marked done.

    PR 109: this ran inside `build_recommendations`, so every read of Today --
    and every warm-up, push evaluation and digest build -- wrote rules. It is
    the same rule as before: being muted means you dealt with THIS firing, so
    the latch is cleared; a rule that fired AGAIN after your mark is new
    information and is left alone (the card un-mutes itself in the build).
    """
    from app.services.recommendations import (
        _at, _dismiss_key, _done_key, _load_map, _parse_dt, load_completed, load_dismissed)
    muted = (await load_dismissed(session, user)) | (await load_completed(session, user))
    if not muted:
        return []
    marks = {**await _load_map(session, _dismiss_key(user)), **await _load_map(session, _done_key(user))}
    rules = (await session.scalars(
        select(TradingRule).where(TradingRule.subject == user.email,
                                  TradingRule.active.is_(True),
                                  TradingRule.triggered.is_(True)))).all()
    healed = []
    for r in rules:
        cid = f"rule_{str(r.id)[:8]}"
        if cid not in muted:
            continue
        dealt = _at(marks.get(cid))
        fired = r.last_triggered_at
        if fired is not None and dealt:
            fired_aw = fired if fired.tzinfo else fired.replace(tzinfo=timezone.utc)
            if fired_aw > _parse_dt(dealt):
                continue
        try:
            await resolve_rule(session, user, str(r.id)[:8], "acknowledged")
            healed.append(cid)
        except Exception:  # noqa: BLE001 -- one rule must not stop the others
            logger.warning("could not resolve dismissed rule %s", cid, exc_info=False)
    return healed


async def evaluate_user(session: AsyncSession, user: User, *, notify: bool = False) -> list[dict]:
    try:   # PR 104d: rule firing honours the user's thresholds (take-profit slice, band)
        from app.services import thresholds
        from app.services.plan_service import get_plan
        thresholds.use(await get_plan(session, user))
    except Exception:  # noqa: BLE001
        pass
    """Update peaks, latch newly-triggered rules, optionally push. Returns the
    list of rules that newly triggered this run."""
    idx, _nav = await _positions_index_and_nav(session, user)
    # Delete rules for holdings that are gone before evaluating anything, so a
    # sold position can never fire a rule set against a price it no longer has.
    await purge_orphan_rules(session, user, idx)
    # PR 109: moved here from the Today build, so a read never writes a rule.
    await heal_dismissed_rules(session, user)
    rules = (await session.scalars(
        select(TradingRule).where(TradingRule.subject == user.email,
                                  TradingRule.active.is_(True)))).all()
    from app.services import advanced_rules as _adv
    if any(r.rule_type in _adv.ADV_TYPES for r in rules):
        await _adv.augment(session, user, idx, _nav, rules)
    newly = []
    retired: list[dict] = []
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
        if not pos and r.rule_type in ENTRY_TYPES and r.ticker.upper() != "CASH":
            # PR 109 (D-b): a WATCH ALERT on a ticker you don't hold. Quote it
            # and evaluate it like any other rule; store the price on the rule
            # so Today can read it without a network call. No quote: it stays
            # armed and waits -- never retired for not being held.
            _px = await offload(_quote_watch, r.ticker)
            if not _px:
                continue
            r.params = {**(r.params if isinstance(r.params, dict) else {}),
                        "watch_price": round(float(_px), 4), "watch_at": _now().isoformat()}
            pos = _watch_pos(r)
        if not pos:
            # The thing this rule watches is no longer a tradeable holding --
            # sold, or (for CASH) never one to begin with. Retire it.
            #
            # This used to retire ONLY a rule that had already latched
            # `triggered`, so an un-fired rule on a sold position stayed armed
            # forever. Seen live: TQQQ, META and AMZN each carrying up to four
            # "armed" rules for positions that were not in the book at all.
            #
            # That is worse than clutter. A stop-loss on AMZN at 222.58, set
            # months ago against a price that has moved since, sits dormant and
            # then goes live the instant the position is re-bought -- firing at a
            # level nobody chose for today's market. A stale stop that reanimates
            # is more dangerous than no stop.
            #
            # Retired, not deleted: `active = False` stops evaluation and takes it
            # out of the armed list, while the rule and its RuleEvent history
            # survive so nothing the user configured vanishes silently. They can
            # re-arm it or delete it.
            if r.active:
                r.active = False
                r.triggered = False
                _reason = ("cash is not a tradeable holding" if r.ticker.upper() == "CASH"
                           else f"retired: {r.ticker} is no longer in your portfolio")
                if not (r.note or "").startswith("retired:") and r.ticker.upper() != "CASH":
                    r.note = _reason[:160]
                retired.append({"id": str(r.id), "ticker": r.ticker,
                                "rule_type": r.rule_type, "reason": _reason})
            elif r.triggered:
                r.triggered = False
            continue
        cur, cost, w = pos["price"], pos["cost"], pos["weight_pct"]
        if r.rule_type == "trailing_stop" and cur > 0:
            r.peak_price = max(r.peak_price or cur, cur)
        if r.rule_type in _adv.ADV_TYPES:
            _p = _adv.params_of(r)
            if r.rule_type == "profit_protect" and cur > 0 and cost > 0:
                # Dormant until the gain is real; from then the peak ratchets.
                _act = float(_p.get("activation_pct") or _adv.DEFAULTS["activation_pct"])
                if not _p.get("activated") and cur >= cost * (1 + _act / 100.0):
                    _p["activated"] = True
                    _p["activated_at"] = _now().isoformat()
                    r.params = dict(_p)
                    r.peak_price = cur
                elif _p.get("activated"):
                    r.peak_price = max(r.peak_price or cur, cur)
            if _p.get("await_clear"):
                # A standing rule that was dealt with waits for its condition to
                # clear before it may fire again -- or the drawdown guard would
                # offer a de-risk every 30 minutes of the same drawdown.
                if _adv.clears(r, pos):
                    _p.pop("await_clear", None)
                    r.params = dict(_p)
                    if r.rule_type == "drawdown_guard":
                        from app.services import buyback_hold as _bh
                        await _bh.release_for_rule(session, user, str(r.id))
                continue
        hit, title, action, target = _evaluate(r, cur, cost, w, pos.get("value_ils") or 0.0,
                                               pos=pos)
        if hit and not r.triggered:
            r.triggered = True
            r.last_triggered_at = _now()
            # One row per firing: this is the record that survives the card being
            # accepted, ignored or regenerated.
            _plan = execution_plan(r, pos)
            event = RuleEvent(
                subject=user.email, rule_id=str(r.id), ticker=r.ticker,
                rule_type=r.rule_type, trigger_price=cur, target_price=target,
                title=title, outcome="triggered",
                action={"plan": _plan or {}},
            )
            session.add(event)
            newly.append({"id": str(r.id), "ticker": r.ticker, "rule_type": r.rule_type,
                          "title": title, "action": action, "event": event,
                          # Whether there is a trade behind the firing. A cap
                          # breached by 0.2 points produces a sub-minimum trim
                          # and no plan -- worth a card, not an interruption.
                          # PR 81: an advanced rule's card is built with more
                          # context (a funded band buy, a de-risk, a stop) and is
                          # always actionable -- so it always alerts.
                          "actionable": bool(_plan) or r.rule_type in _adv.ADV_TYPES})
        elif (not hit and r.triggered and r.rule_type in _adv.STANDING
                and _adv.clears(r, pos)):
            r.triggered = False
        elif (not hit and r.triggered
                and r.rule_type in ("price_above", "price_below", "max_weight")
                and clears_with_margin(r, cur, w, pos.get("value_ils") or 0.0)):
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
        # ONE governed push path.
        #
        # This called send_to_subject directly, with no dedupe and no rate limit,
        # while P4.2 built exactly that machinery for every other trigger. Two
        # notification paths, one governed and one not -- the same shape as the
        # duplicated reprice loop that mispriced the cash row. The fix is not a
        # second limiter here; it is routing this through the existing one.
        #
        # And nothing is pushed that has no action behind it: a cap breached by
        # 0.2 points is a trim the app itself declines to propose, so waking the
        # user for it is the sub-minimum tax card again, in a form he cannot
        # dismiss. It still records a RuleEvent and still shows a card.
        from app.services import push_service
        for n in newly:
            if not n.get("actionable", True):
                logger.info("rule %s %s fired with no actionable trade; card only, no push",
                            n["ticker"], n["rule_type"])
                continue
            trigger, hours = push_service.classify_trigger(
                {"id": f"rule_{n['id']}", "rule_id": n["id"], "title": n["title"]})
            sig = push_service.trigger_signature(trigger, {"rule_id": n["id"]})
            if hours is None or await push_service._seen_within(  # noqa: SLF001
                    session, user.email, sig, hours):
                continue
            sent = await push_service.send_to_subject(
                session, user.email, n["title"], n["action"], url="/app/", tag=sig)
            n["event"].notified = bool(sent)
            if sent:
                await push_service._mark(session, user.email, sig)  # noqa: SLF001
        await session.commit()
    for n in newly:
        n["event_id"] = str(n["event"].id)
        n.pop("event", None)
    if retired:
        logger.info("retired %d rule(s) on positions no longer held: %s", len(retired),
                    ", ".join(f"{r['ticker']} {r['rule_type']}" for r in retired))
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
_ONE_SHOT = {"stop_loss", "take_profit", "trailing_stop", "buy_dip", "profit_protect",
             "buy_back"}

# The outcomes that mean "the slice was sold". `executed` is what Accept sends;
# `accepted` is kept for any older caller.
_LADDER_OUTCOMES = {"executed", "accepted"}

# Rule types a "Keep & update" choice can re-level, and which way the level
# moves. Only take-profit reaches the rule-vs-sleeve decision today (it is the
# only rule whose card is a `trim`); the others are here so the helper answers
# honestly if one ever does.
_KEEP_UPWARD = {"take_profit", "price_above"}
_KEEP_DOWNWARD = {"stop_loss", "price_below"}


def keep_level(rule, price: float, cost: float) -> float | None:
    """Where a rule re-arms when the user keeps the position instead of selling.

    Eran, 2026-09-28: when a sell rule fights a sleeve target, "Keep" must UPDATE
    the rule, not retire it. The update has one hard requirement: the new level
    must not already be breached, or the rule fires again on the next
    30-minute evaluation and the decision was for nothing -- which is exactly
    what happened on SOXL, re-armed at 148.52 with the price above it.

    Upward rules take the ladder's next rung, or one full step above today,
    whichever is higher. Downward rules sit one step below today. Returns the
    level in the rule's OWN unit (price, or % against cost in `pct` mode), or
    None for a rule type this does not re-level.
    """
    rt, mode = getattr(rule, "rule_type", None), str(getattr(rule, "mode", "price"))
    lvl = float(getattr(rule, "level", 0) or 0)
    price, cost = float(price or 0), float(cost or 0)
    step = TAKE_PROFIT_LADDER_STEP
    if rt in _KEEP_UPWARD:
        if mode == "pct":
            gain_now = ((price / cost) - 1.0) * 100.0 if cost > 0 and price > 0 else lvl
            return round(max(next_rung(lvl, "pct"), gain_now + step * 100.0), 4)
        return round(max(next_rung(lvl, "price"), price * (1.0 + step)), 4)
    if rt in _KEEP_DOWNWARD:
        if mode == "pct":
            loss_now = (1.0 - (price / cost)) * 100.0 if cost > 0 and price > 0 else abs(lvl)
            return round(max(0.0, loss_now + step * 100.0), 4)
        return round(price * (1.0 - step), 4)
    return None


async def keep_and_relevel(session: AsyncSession, user: User, rule_id_or_prefix: str,
                           level: float, *, reason: str = "") -> dict:
    """Resolve a firing as KEPT: the position stays, the rule moves to `level`.

    The rule stays active and is un-latched, the open event is stamped `kept`
    with the old and new level, and nothing in the book changes. A level that
    is already breached is refused rather than written, for the reason in
    `keep_level`.
    """
    rid = str(rule_id_or_prefix)
    rule = None
    for r in (await session.scalars(
            select(TradingRule).where(TradingRule.subject == user.email))).all():
        if str(r.id) == rid or str(r.id).startswith(rid):
            rule = r
            break
    if rule is None:
        return {"ok": False, "error": "rule not found"}
    idx = await _positions_index(session, user)
    pos = idx.get(str(rule.ticker).upper()) or {}
    price, cost = float(pos.get("price") or 0), float(pos.get("cost") or 0)
    level = float(level)
    if level <= 0:
        return {"ok": False, "error": "the new level must be positive"}
    # Would the new level fire straight away? Evaluate it exactly as the
    # 30-minute job would, rather than re-deriving the comparison here.
    old = float(rule.level)
    rule.level = level
    still, _t, _a, _tgt = _evaluate(rule, price, cost, float(pos.get("weight_pct") or 0),
                                    float(pos.get("value_ils") or 0.0))
    if still:
        rule.level = old
        return {"ok": False, "error": (
            f"{level:g} is already reached at today's price ({price:,.2f}), so the rule "
            f"would fire again immediately. Pick a level past today's price.")}
    await record_outcome(session, user, str(rule.id), "kept",
                         {"kind": "keep_and_relevel", "old_level": old,
                          "new_level": level, "reason": reason[:200]})
    rule.triggered = False
    rule.active = True
    unit = "%" if rule.mode == "pct" else ""
    rule.note = (f"Kept {rule.ticker}; moved from {old:g}{unit} to {level:g}{unit}.")[:160]
    await session.commit()
    return {"ok": True, "rule_id": str(rule.id), "ticker": rule.ticker,
            "rule_type": rule.rule_type, "old_level": old, "new_level": level,
            "mode": rule.mode}


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

    # A FIRING THAT WAS ALREADY DEALT WITH IS NOT DEALT WITH TWICE.
    #
    # Accept resolves a rule card twice: `apply_recommendation` stamps it
    # `executed` (and re-arms a take-profit on the next rung), then
    # `complete_recommendation` stamps the same card `acknowledged`. The second
    # call used to fall through to the one-shot branch and switch the freshly
    # re-armed ladder OFF. Nothing is open any more, so there is nothing to do.
    if str(outcome) == "acknowledged" and not rule.triggered:
        return True

    await record_outcome(session, user, str(rule.id), outcome, action)
    rule.triggered = False
    if rule.rule_type == "take_profit" and str(outcome) in _LADDER_OUTCOMES:
        # THE LADDER. A take-profit that fired sold a SLICE, not the position,
        # so the rule is not spent -- it has a next rung. Re-arming it higher is
        # what makes a winner get sold into strength in stages instead of either
        # being liquidated at the first rung or never trimmed again.
        #
        # Only when the user SOLD the slice. A rule the user DECLINED is one
        # they disagreed with, and moving its level up would quietly re-ask the
        # same question from a worse place; that still latches off, as before.
        #
        # PR 72: this read `== "accepted"`, and nothing in the app ever sends
        # "accepted" -- Accept on a rule card sends "executed". So the ladder
        # never ran. Measured live on rule 8cd9f1ff (SOXL): executed at 148.52
        # on 2026-09-22 18:40, still 148.52 when it fired again on 09-25.
        rule.level = next_rung(rule.level, rule.mode)
        rule.active = True
        rule.note = (f"Ladder: next slice if it reaches "
                     f"{rule.level:.2f}{'%' if rule.mode == 'pct' else ''}.")[:160]
        logger.info("take_profit %s re-armed at %s", rule.ticker, rule.level)
    elif rule.rule_type in ("rebalance_band", "trend_break", "drawdown_guard"):
        # PR 81: standing, but dealt with -- wait for the condition to clear.
        _p = dict(rule.params or {})
        _p["await_clear"] = True
        rule.params = _p
    elif rule.rule_type in _ONE_SHOT:
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
        fired = await evaluate_user(session, user, notify=True)
        total += len(fired)
        # PR 97: a protective rule firing IS the market changing -- re-solve
        # the goal now rather than at the nightly run, so Today's goal cards
        # speak about the book as it is after the event.
        if fired:
            try:
                from app.services.frontier_service import resolve_after_rule
                await resolve_after_rule(session, user, fired)
            except Exception:  # noqa: BLE001 -- a re-solve must never cost the rule evaluation
                logger.warning("frontier re-solve after a rule fired failed", exc_info=False)
    return {"subjects": len(subjects), "triggered": total}


# --------------------------------------------------------------------------- #
# Recommendations surfacing (triggered rules -> 'What to do now')
# --------------------------------------------------------------------------- #
def _cash_landing(cash_after: float, nav: float, target_frac: float) -> str:
    """What the proceeds mean once they are cash. The "and then what" line.

    Two honest endings and no invented third. Above the target, the money is
    money the book wants deployed and another card is built to size that; below
    it, it is the reserve doing its job. Proposing a specific replacement buy
    here would be a second sizing decision inside a card that already has one --
    the shape PR 60 fixed when two cards wanted the same 7,476.
    """
    if nav <= 0:
        return "The proceeds stay in cash until you decide where they go."
    pct = cash_after / nav
    if target_frac and pct > target_frac:
        return (f"That takes cash to {pct:.1%} against your {target_frac:.1%} target — "
                f"above it, so the “Put your cash to work” card will size where it goes.")
    if target_frac:
        return (f"That takes cash to {pct:.1%} against your {target_frac:.1%} target — "
                f"still at or under it, so it stays as dry powder.")
    return f"That takes cash to {pct:.1%} of the book."


async def triggered_rule_recs(session: AsyncSession, user: User) -> list[dict]:
    rules = (await session.scalars(
        select(TradingRule).where(TradingRule.subject == user.email,
                                  TradingRule.active.is_(True),
                                  TradingRule.triggered.is_(True)))).all()
    idx, _nav = (await _positions_index_and_nav(session, user)) if rules else ({}, 0.0)
    from app.services import advanced_rules as _adv
    if rules:
        await _adv.augment(session, user, idx, _nav, rules)
    # WHERE THE MONEY LANDS. A trim credits cash; a card that does not say what
    # that leaves cash at has answered "how much to sell" and not "and then
    # what". Eran, 2026-09-06: "it doesn't tell me what do do instead (buy
    # something else or keep it in cash)". Read once, outside the loop.
    _cash_ils, _cash_target = 0.0, 0.0
    if rules:
        try:
            from app.services.funding_service import cash_floor_pct
            from app.services.intake_service import get_cash
            from app.services.plan_service import get_plan
            _plan = await get_plan(session, user)
            _cash_ils = float(await get_cash(session, user) or 0.0)
            from app.services.fx import book_nav_ils
            from app.services.intake_service import list_positions as _lp
            _cash_target = float(cash_floor_pct(
                getattr(_plan, "objective", None), _plan,
                book_nav_ils(await _lp(session, user))) or 0.0)
        except Exception:  # noqa: BLE001 - the trade is the card; the sentence is not
            logger.warning("cash context unavailable for rule cards", exc_info=True)
    out = []
    _cleared = False
    # PR 109: an exit rule on a position you no longer hold gets no card (the
    # evaluator clears it within 30 minutes); the read path used to delete it.
    if idx:
        rules = [r for r in rules if not _is_orphan_exit(r, set(idx))]
    for r in rules:
        # PR 109: a watch alert (D-b) is priced from what the evaluator stored;
        # at price 0 a fired `price_above` read as "back under" and was cleared.
        pos = (idx.get(r.ticker.upper())
               or (_watch_pos(r) if r.rule_type in ENTRY_TYPES else None)
               or {"price": 0, "cost": 0, "qty": 0, "weight_pct": 0})
        hit, title, action, _t = _evaluate(r, pos["price"], pos["cost"], pos["weight_pct"],
                                           pos.get("value_ils") or 0.0, pos=pos)
        if r.rule_type in _adv.ADV_TYPES:
            if not hit:
                if r.rule_type in _adv.STANDING and _adv.clears(r, pos):
                    r.triggered = False
                    _cleared = True
                continue
            if execution_plan(r, pos) is None:
                # A band BUY, a drawdown de-risk, or a trend with no measured
                # edge: `advanced_rules.extra_cards` builds those with the
                # context this function does not have.
                continue

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
        #
        # The card and the latch are two different questions, and writing both
        # in one line is what let the MSFT cap notify every few hours. The card
        # must go the instant the condition is false -- the screen has to be
        # honest about NOW. The latch governs notification, so clearing it on
        # the first tick back under the cap re-arms the push, and a position
        # sitting on its own boundary crosses it all day. Hence: drop the card
        # immediately, re-arm the alert only on a real move.
        if not hit and r.rule_type in ("price_above", "price_below", "max_weight"):
            if clears_with_margin(r, pos["price"], pos["weight_pct"],
                                  pos.get("value_ils") or 0.0):
                r.triggered = False
                _cleared = True
            continue
        plan = execution_plan(r, pos)
        price = float(pos.get("price") or 0)
        if plan:
            shares = plan["shares"]
            verb = "Sell all" if plan["kind"] == "sell_position" else "Trim"
            # IN SHEKELS, NET OF TAX, THROUGH THE FUNCTION ACCEPT RUNS.
            #
            # This was `shares * price` -- the position's NATIVE currency, with
            # no FX and no tax, rendered without a symbol on a screen where every
            # other number is shekels. On SOXL at $117.28 a six-share trim reads
            # "~737" while Accept credits about 2,700 to cash. Same defect as
            # `_rebalance_to`'s "sell 4,324" against a 12,139 execution, one
            # order of magnitude quieter.
            from app.services.recommendations import _plan_sale, _sale_value_ils
            if pos.get("lots"):
                _pl = _plan_sale(pos["lots"], shares)    # the lots Accept will take
                gross, tax = _pl["gross"], _pl["tax"]
            else:
                gross, tax = _sale_value_ils(shares, price, float(pos.get("cost") or 0.0),
                                             pos.get("market"), pos.get("meta"))
            net = max(0.0, gross - tax)
            action = (f"{verb} {shares:g} {r.ticker} at {price:,.2f} "
                      f"— about ₪{gross:,.0f}, the rule you set.")
            how = [f"Accept executes this: {verb.lower()} {shares:g} {r.ticker} in your "
                   "InvestWise book and credits the net-of-tax proceeds to cash.",
                   (f"About ₪{net:,.0f} lands in cash"
                    + (f" after an estimated ₪{tax:,.0f} of tax" if tax >= 1 else "")
                    + "."),
                   _cash_landing(_cash_ils + net, _nav, _cash_target),
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
                    # PR 70: the shekels this firing moves, for the priority
                    # score and the mute marks. Same gross the action quotes.
                    "est_amount": (round(gross, 2) if plan else None),
                    "apply": {**(plan or {"kind": "none"}), "rule_id": str(r.id)},
                    # WHEN this firing happened travels with the card.
                    #
                    # Without it a consumer can only ask "is this rule
                    # triggered", never "is this the SAME firing I already dealt
                    # with". The suppression path in `recommendations` needs the
                    # second question: dismissing a card says "I handled THIS
                    # firing", and a later firing is new information that has to
                    # reach the screen rather than being auto-acknowledged
                    # against a dismissal from three weeks ago.
                    "meta": {"triggered_at": (r.last_triggered_at.isoformat()
                                              if r.last_triggered_at else None),
                             # PR 70: the priority score treats a fired stop as
                             # always Important, so the card says which rule it is.
                             "rule_type": r.rule_type, "ticker": r.ticker,
                             # PR 81: a protective exit is never suppressed or
                             # negotiated against a plan target.
                             "protective": r.rule_type in _adv.PROTECTIVE},
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
                      strategy_id: str | None = None,
                      params: dict | None = None) -> TradingRule:
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
    from app.services import advanced_rules as _adv
    if rule_type in _adv.ADV_TYPES:
        mode = "pct"
        level, params = _adv.validate(rule_type, level, params)
        if rule_type == "drawdown_guard":
            ticker = _adv.BOOK          # the whole book, not a holding
    ticker = ticker.strip().upper()

    # Re-level rather than stack. See _ONE_PER_TICKER for why.
    existing = await _conflicting_rule(session, user, ticker, rule_type, mode, strategy_id)
    if existing is not None:
        existing.level = float(level)
        existing.mode = mode
        existing.active = True
        existing.triggered = False       # a re-levelled rule re-arms
        if note:
            existing.note = note[:160]
        if strategy_id:
            existing.strategy_id = strategy_id
        if params is not None:
            existing.params = dict(params)
        await session.commit()
        return existing

    rule = TradingRule(subject=user.email, ticker=ticker,
                       rule_type=rule_type, mode=mode, level=float(level),
                       note=(note[:160] if note else note), strategy_id=strategy_id,
                       params=(dict(params) if params else None))
    session.add(rule)
    await session.commit()
    return rule


async def conflicting_rule_level(session: AsyncSession, user: User, ticker: str,
                                 rule_type: str, mode: str,
                                 strategy_id: str | None = None) -> float | None:
    """The level a create_rule call would REPLACE, or None if it would add one.

    Read before the write, so a confirmation can say "re-levelled 12% -> 15%"
    instead of claiming it armed a rule that already existed.
    """
    row = await _conflicting_rule(session, user, (ticker or "").strip().upper(),
                                  rule_type, mode, strategy_id)
    return float(row.level) if row is not None else None


async def _conflicting_rule(session: AsyncSession, user: User, ticker: str,
                            rule_type: str, mode: str, strategy_id: str | None):
    """The active rule this one would duplicate, or None.

    A `strategy_signal` is keyed by its mode and the strategy it follows: an
    entry and an exit on one ticker are both wanted, and two sleeves may
    legitimately subscribe to the same ticker's signal.
    """
    if rule_type not in _ONE_PER_TICKER and rule_type != "strategy_signal":
        return None
    q = select(TradingRule).where(TradingRule.subject == user.email,
                                  TradingRule.ticker == ticker,
                                  TradingRule.rule_type == rule_type,
                                  TradingRule.active.is_(True))
    if rule_type == "strategy_signal":
        q = q.where(TradingRule.mode == mode, TradingRule.strategy_id == strategy_id)
    return (await session.scalars(q)).first()


async def name_caps(session: AsyncSession, user: User) -> dict[str, float]:
    """Ticker -> its own ``max_weight`` cap, as a FRACTION of NAV.

    THE CAP THAT ALERTED AND NEVER CONSTRAINED. A ``max_weight`` rule has always
    fired a card once a position was already over it, and played no part in
    deciding how much to buy or what to trim -- so the app would size a purchase
    straight through the limit and then tell you it had been breached.

    Measured on a reproduction, 2026-09-06: NVDA at 10% of the book with a
    hand-set 12% cap, Equities under its Grow target so the buy was legitimate.
    `propose_funded_buy` sized **4,500**, taking NVDA to **40.0%** -- bounded
    only by the plan-wide `concentration_cap`, 28 points past the cap the user
    had set. On the sell side the same position at 20% against its 12% cap
    ranked BELOW an overweight BND, carrying the reason "corrects no drift",
    because `rank_trim_candidates` measured `weight - cap` against the plan-wide
    number too.

    V5 and V6 called this "a hand-set cap plays no part in what gets trimmed".
    It is both sides, and the buy side is the one that can create the breach.

    UNITS. ``trading_rules.level`` is a PERCENTAGE for this rule type --
    `_evaluate` compares it to `weight_pct` directly. Every consumer of caps in
    `funding_service` works in FRACTIONS, like `concentration_cap`. The /100
    happens once, here.

    Only ACTIVE rules. A disabled cap is a cap the user turned off, and a
    constraint that outlives its own switch is worse than no constraint.
    """
    rows = (await session.scalars(
        select(TradingRule).where(TradingRule.subject == user.email,
                                  TradingRule.rule_type == "max_weight",
                                  TradingRule.active.is_(True)))).all()
    out: dict[str, float] = {}
    for r in rows:
        tk = (r.ticker or "").upper()
        try:
            lvl = float(r.level or 0.0) / 100.0
        except (TypeError, ValueError):
            continue
        if not tk or lvl <= 0:
            continue
        # TWO CAPS ON ONE TICKER: the tighter one wins. `_ONE_PER_TICKER`
        # already makes this unreachable through the normal paths, and a
        # constraint that silently picks the looser of two limits is the wrong
        # way to be wrong if it ever becomes reachable again.
        out[tk] = min(out[tk], lvl) if tk in out else lvl
    return out


async def list_rules(session: AsyncSession, user: User) -> list[dict]:
    rules = (await session.scalars(
        select(TradingRule).where(TradingRule.subject == user.email)
        .order_by(TradingRule.created_at.desc()))).all()
    idx, _lnav = (await _positions_index_and_nav(session, user)) if rules else ({}, 0.0)
    # PR 109: A READ NEVER WRITES. This deleted rules for holdings that are gone
    # at read time, so one GET of Today removed watch alerts and stops before
    # any card showed. Orphaned EXIT rules are now hidden here and cleared by
    # the evaluator (30 min); watch alerts (D-b) are listed with their last
    # quoted price. Guarded as before: with an empty index nothing is hidden.
    if rules and idx:
        rules = [r for r in rules if not _is_orphan_exit(r, set(idx))]
    from app.services import advanced_rules as _adv
    if rules:
        await _adv.augment(session, user, idx, _lnav, rules)
    out = []
    for r in rules:
        pos = idx.get(r.ticker.upper())
        if pos is None and r.rule_type in ENTRY_TYPES:
            pos = _watch_pos(r)

        # PR 109: the "legacy safety net" that RETIRED a rule here (active=False
        # on a read) is gone. Orphaned exit rules are hidden above; the evaluator
        # retires or deletes them. A read that writes is how one GET of Today
        # could switch off a stop.

        cur = pos["price"] if pos else None
        hit, _title, _action, target = _evaluate(
            r, pos["price"], pos["cost"], pos["weight_pct"], pos.get("value_ils") or 0.0,
            pos=pos) if pos else (False, "", "", None)
        status = _adv_status(r, pos) if (pos and r.rule_type in _adv.ADV_TYPES) else None

        # Report the quantity the rule ACTUALLY watches, and its unit.
        #
        # This returned `current_price` for every rule type, so a max_weight rule
        # rendered as "now 137.61 -> 10.00": SOXL's dollar price against a
        # percent-of-portfolio cap. Two different quantities on one row, and it
        # reads as a thirteenfold breach when the position is at 9.7% of a 10%
        # cap. The number was never wrong, it was never comparable.
        if r.rule_type in ("max_weight", "rebalance_band"):
            current, unit = ((pos["weight_pct"] if pos else None), "pct_of_portfolio")
        elif r.rule_type == "drawdown_guard":
            current, unit = (((pos or {}).get("book") or {}).get("dd_pct"), "pct_drawdown")
        elif r.rule_type == "trend_break":
            current, unit = (None, "trend")
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
                    # PR 81: what an advanced rule is doing right now, in words.
                    "status": status, "params": r.params,
                    "last_triggered_at": r.last_triggered_at.isoformat() if r.last_triggered_at else None})
    return out


def _adv_status(r, pos: dict) -> str | None:
    """One line for the Rules row of an advanced rule."""
    from app.services import advanced_rules as _adv
    L = float(r.level or 0)
    if r.rule_type == "rebalance_band":
        t = pos.get("plan_target_pct")
        if t is None:
            return "no plan target for this holding yet"
        return f"{float(pos.get('weight_pct') or 0):.1f}% vs plan {t:.1f}% ±{L:g}"
    if r.rule_type == "profit_protect":
        p = _adv.params_of(r)
        act = float(p.get("activation_pct") or _adv.DEFAULTS["activation_pct"])
        cost, cur = float(pos.get("cost") or 0), float(pos.get("price") or 0)
        if not p.get("activated"):
            gain = ((cur / cost) - 1) * 100 if cost > 0 else 0.0
            return f"dormant: {gain:+.1f}% of +{act:g}% to arm"
        peak = float(r.peak_price or cur)
        return f"armed: peak {peak:,.2f}, stop {peak * (1 - L / 100):,.2f}"
    if r.rule_type == "trend_break":
        tr = pos.get("trend") or {}
        m = tr.get("metrics") or {}
        if not tr.get("fresh") or not m.get("ok"):
            return "waiting for tonight's measurement"
        sig = _adv.signal_of(r)
        edge = (tr.get(sig) or {}).get("edge")
        return (f"{'50/200' if sig == 'long' else '20/50'}-day trend {m.get(sig)}"
                f" · selling on it {'has' if edge else 'has not'} beaten holding")
    if r.rule_type == "buy_back":
        if float(r.level or 0) <= 0:
            return "holds re-buys until your drawdown guard re-arms"
        cur = float(pos.get("price") or 0)
        t = pos.get("plan_target_pct")
        held = ""
        if t is not None and pos.get("nav"):
            amt = max(0.0, (float(t) - float(pos.get("weight_pct") or 0)) / 100.0 * float(pos["nav"]))
            # The gap to the PLAN target, not cash: the cash actually reserved is
            # capped by what you hold above the floor, and the Today card shows
            # that (live: this row said "₪1,995 of cash waiting" beside a card
            # reserving ₪1,713).
            held = f"plan wants ₪{amt:,.0f} more · "
        gap = (cur / float(r.level) - 1.0) * 100.0 if r.level else 0.0
        return f"{held}{cur:,.2f} now ({gap:+.1f}% vs the level)"
    if r.rule_type == "drawdown_guard":
        b = pos.get("book") or {}
        if b.get("dd_pct") is None:
            return "needs a few days of snapshots"
        return f"{b['dd_pct']:.1f}% below the peak ({b.get('peak_on')}) · guard {L:g}%"
    return None


async def suggest_rules_for_holdings(session: AsyncSession, user: User) -> list[dict]:
    """Per-holding suggested rule *set* with concrete, volatility-derived levels.

    Nothing is saved: each suggestion is a ready-to-arm rule the UI can add with
    one click (or 'add all') via the normal create-rule endpoint. Levels are
    grounded in the holding's own realized volatility, never invented; rule types
    the user has already armed are omitted so suggestions stay fresh.
    """
    idx = await _positions_index(session, user)
    if not idx:
        return []
    # EVERY rule counts, armed or paused. PR 104a.2: Eran accepted "Retune your
    # protection" (five stops paused for his goal) and Today answered with "4
    # protective rules ready to arm" -- the same stops, offered back, because
    # only ARMED rules were counted. A rule you paused, or one that fired and was
    # dealt with, is a decision about that holding; re-arming it is one tap on
    # the Rules tab, and re-suggesting it is the app arguing with itself.
    existing = (await session.scalars(
        select(TradingRule).where(TradingRule.subject == user.email))).all()
    have = {(r.ticker.upper(), r.rule_type) for r in existing}
    # A CAP MAY NEVER BE SUGGESTED BELOW THE SLEEVE TARGET IT WOULD FIGHT.
    #
    # This is where the 15% TQQQ cap came from. `cap = max(15, 5*ceil(w/5))` is
    # derived from the position's CURRENT weight and knows nothing about
    # sleeves, so it was suggested while TQQQ was small, armed, and then the
    # `btm_swing_dip` sleeve grew to 38.4% of the book. From that day the app
    # told him every hour to trim a holding it was simultaneously targeting,
    # and the loudest card on Today was "TQQQ is 39% of your portfolio - trim
    # 24.231 shares" against a sleeve sitting exactly on target.
    #
    # Suggesting a cap under the target is not a judgement call the user should
    # have to catch. It is the app disagreeing with itself.
    #
    # PR 79: and never below the CORE's target either. The VTI cap at 15% was
    # suggested at VTI 12% with an 18.2% core target, and fought the core
    # realign for days. The floor is now the whole plan (sleeves + core).
    try:
        from app.services.recommendations import plan_ticker_targets
        sleeve_floor = {tk.upper(): round(w * 100.0, 1)
                        for tk, w in (await plan_ticker_targets(session, user)).items()}
    except Exception:  # noqa: BLE001 -- a floor is never worth failing suggestions over
        sleeve_floor = {}
    # Everything above is DB work; everything in _suggestions_from is 200 days
    # of history PER TICKER through synchronous urllib. Phase B offloaded the
    # agents that looked blocking at their call site and missed this one,
    # because `await suggest_rules_for_holdings(...)` reads as non-blocking --
    # and an await yields nothing when the coroutine blocks inside itself.
    # Measured cost of that miss: ~1s of held event loop, half the time.
    #
    # `idx`, `have` and `sleeve_floor` are plain dicts/sets, not ORM rows, so the
    # columns-yes-relationships-never rule in app/core/offload.py holds.
    return await offload(_suggestions_from, idx, have, sleeve_floor)


def _suggestions_from(idx: dict[str, dict], have: set,
                      sleeve_floor: dict[str, float] | None = None) -> list[dict]:
    """The blocking half of suggest_rules_for_holdings. Pure: no session."""
    sleeve_floor = sleeve_floor or {}
    from app.providers.registry import guarded_history
    from app.services.recommendations import stop_buffer_pct

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
        _plan_t = sleeve_floor.get(tk.upper())
        if _plan_t and (tk, "max_weight") not in have:
            # (a cap you already set is offered as a band by the one-time card)
            # PR 81: a holding your plan targets gets a BAND around that target,
            # not a cap -- a cap only trims, at a number set once; a band
            # follows the plan both ways and cannot fight it.
            cand.append({"rule_type": "rebalance_band", "mode": "pct", "level": 3.0,
                         "label": "\u2696\ufe0f Rebalance band \u00b13",
                         "why": (f"Your plan targets {_plan_t:g}% of the book in {tk}. Trims "
                                 f"above {_plan_t + 3:g}% and tops up below {_plan_t - 3:g}%, "
                                 f"back to the target.")})
        elif weight >= 12:
            cap = float(max(15.0, 5 * math.ceil(weight / 5)))
            floor = sleeve_floor.get(tk.upper())
            if floor and floor + 1.5 > cap:
                # A sleeve wants MORE of this than the weight-derived cap allows.
                # The cap is not wrong about concentration; it is wrong about
                # THIS book, where the concentration is the strategy. Lift it
                # above the target and name the reason on the suggestion.
                cap = float(5 * math.ceil((floor + 1.5) / 5))
                why = (f"Your plan targets {floor:g}% of the book in {tk}, so this caps it at "
                       f"{cap:.0f}% — above the target, firing only if it runs past it.")
            else:
                why = f"It's {weight:.0f}% of your book — trim back toward {cap:.0f}% if it grows."
            cand.append({"rule_type": "max_weight", "mode": "pct", "level": cap,
                         "label": f"\u2696\ufe0f Max weight {cap:g}%", "why": why})

        fresh = [c for c in cand if (tk, c["rule_type"]) not in have]
        for c in fresh:
            c["ticker"] = tk
        if fresh:
            out.append({"ticker": tk, "current_price": round(price, 2),
                        "weight_pct": weight, "rules": fresh})
    out.sort(key=lambda h: h["weight_pct"], reverse=True)
    return out


async def plan_target_pct(session: AsyncSession, user: User, ticker: str) -> float | None:
    """The plan's target for one ticker, in % of the book (sleeves + core), or None."""
    try:
        from app.services.recommendations import plan_ticker_targets
        t = (await plan_ticker_targets(session, user)).get(str(ticker).upper())
    except Exception:  # noqa: BLE001
        return None
    return round(float(t) * 100.0, 2) if t else None


async def cap_below_plan(session: AsyncSession, user: User, ticker: str,
                         rule_type: str, level: float) -> dict | None:
    """PR 79 guard (Eran: warn, let me save). A max-weight cap under the plan's
    own target for that ticker trims the plan every time it fires. Returns the
    warning and a suggested level, or None when there is nothing to warn about."""
    if rule_type != "max_weight":
        return None
    t = await plan_target_pct(session, user, ticker)
    if t is None or float(level) >= t + 0.5:
        return None
    suggested = float(math.ceil(t + 1.5))
    return {"warning": (f"Your plan targets {ticker.upper()} at {t:.1f}% of the book. A "
                        f"{float(level):g}% cap will keep trimming it below your own plan, and "
                        f"the app will buy it back. Suggested: {suggested:g}%."),
            "plan_target_pct": t, "suggested_level": suggested}


async def update_rule(session: AsyncSession, user: User, rule_id: str, *,
                      level: float | None = None, note: str | None = None,
                      force: bool = False, params: dict | None = None) -> dict:
    """PR 79 -- edit a rule's level and note in place (Eran: "I can't edit
    existing rules"). The rule keeps its id, type and history; it is re-armed
    (un-latched and active) so the next evaluation judges the NEW level. A cap
    below the plan target is refused once with a warning; `force` saves it."""
    try:
        rid = uuid.UUID(str(rule_id))
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "rule not found"}
    r = await session.get(TradingRule, rid)
    if not r or r.subject != user.email:
        return {"ok": False, "error": "rule not found"}
    if level is not None:
        level = float(level)
        if r.rule_type == "strategy_signal":
            return {"ok": False, "error": "a strategy-signal rule has no level to edit"}
        if level <= 0:
            return {"ok": False, "error": "the level must be positive"}
        from app.services import advanced_rules as _adv
        if r.rule_type in _adv.ADV_TYPES:
            try:
                level, _ = _adv.validate(r.rule_type, level, None)
            except ValueError as e:
                return {"ok": False, "error": str(e)}
        elif r.mode == "pct" and level >= 100:
            return {"ok": False, "error": "a percentage level must be below 100"}
        if not force:
            warn = await cap_below_plan(session, user, r.ticker, r.rule_type, level)
            if warn:
                return {"ok": False, "needs_confirm": True, **warn}
    old = float(r.level)
    if level is not None:
        r.level = level                  # a trailing stop keeps its high-water mark
    if note is not None:
        r.note = note.strip()[:160] or None
    if params:
        from app.services import advanced_rules as _adv
        try:
            _, clean = _adv.validate(r.rule_type, float(r.level), params)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        merged = dict(r.params or {})
        merged.update(clean or {})
        r.params = merged
    r.triggered = False
    r.active = True
    await session.commit()
    return {"ok": True, "id": str(r.id), "ticker": r.ticker, "rule_type": r.rule_type,
            "mode": r.mode, "old_level": old, "level": float(r.level), "note": r.note,
            "params": r.params}


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
