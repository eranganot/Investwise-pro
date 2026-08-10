"""Apply a strategy (preset + rebalance trades) and fund or load its basket.

Two very different things used to sit one click apart under near-identical
wording. They are now separate modes with separate labels:

* ``mode="fund"`` -- the default for the rule-based "Beat the Market" family.
  A sleeve **coexists** with the rest of the book by definition, so funding it
  raises only the shortfall: spendable cash down to the objective's cash floor
  first, then the worst-fitting holdings ranked by plan fit. Everything not
  named as a funding leg survives untouched.
* ``mode="replace"`` -- the original behaviour, retained for the four static
  model-basket families, where wholesale replacement *is* the point. The caller
  must confirm it against a list of exactly what would be deleted.

Neither mode places a brokerage order. Both update the tracked book only, and
say so.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.engines.allocation_engine import AllocationEngine
from app.models.tables import User
from app.providers.registry import guarded_quote
from app.schemas.intake import IntakePosition
from app.schemas.state_machine import Market
from app.services import strategies as cat
from app.services import strategy_catalog
from app.services.allocation_mix import current_mix
from app.services.funding_service import (
    MIN_TRADE_ILS, describe_funding, plan_funding)
from app.services.fx import fx_rate, price_currency
from app.services.intake_service import (
    delete_position, ensure_account, ensure_entity, get_cash,
    is_cash_position, list_positions, set_cash, upsert_positions)
from app.services.plan_service import effective_caps, get_plan, upsert_plan
from app.services.portfolio_analytics import compute_snapshot

# Modes `load_basket` understands. "fund" is additive, "replace" is destructive.
FUND = "fund"
REPLACE = "replace"

BROKER_NOTE = "Tracked book updated — no brokerage order was placed."

# How far short of the chosen sleeve we may land before refusing to execute.
#
# Measured in POINTS OF NAV, because that is the unit the user actually chose --
# the slider sets a share of the book and the label renders it as a whole
# percent. A gap smaller than one point cannot change the number they were
# looking at when they decided; a gap larger than one point means the sleeve
# they get is not the sleeve they asked for, and installing it anyway is a
# position at a size nobody chose.
#
# Sized against MIN_TRADE_ILS deliberately: the funding engine stops raising
# money once the remainder falls below the minimum worthwhile trade, so it can
# legitimately land up to MIN_TRADE_ILS short. On a small book that is several
# points of NAV -- which is exactly the case that must abstain, and exactly the
# case an absolute-shekel threshold waves through.
SLEEVE_SHORTFALL_TOLERANCE_PCT = 1.0


def _pdicts(rows) -> list[dict]:
    # meta is carried deliberately: it holds `price_currency`, without which an
    # ILS-native cash row would be valued as if it traded in USD.
    return [{"ticker": p.ticker, "market": p.market, "quantity": float(p.quantity),
             "cost_basis": float(p.cost_basis), "current_price": float(p.current_price or 0),
             "meta": p.meta if isinstance(p.meta, dict) else {}} for p in rows]


def _snapshot(rows) -> dict:
    return compute_snapshot(_pdicts(rows)) if rows else {"nav": 0.0, "exposure_ticker": {}}


def _nav(rows) -> float:
    return _snapshot(rows)["nav"] if rows else 0.0


async def _arm_sleeve_cap(session: AsyncSession, user: User, strategy_id: str,
                          sleeve_pct: float | None) -> list[dict]:
    """Arm one ``max_weight`` per aggressive ticker, at the sleeve size.

    Why this exists: ``as_legacy_strategy`` hardcodes ``{"Equities": 1.0}`` for
    every strategy in this family, so ``sleeve_pct`` only ever changed the
    *basket*. Applying at 20% and at 90% wrote an identical plan -- the sleeve
    was a number the app collected and then ignored. Asset-class allocation
    cannot express it either: TQQQ and QQQ are both Equities.

    So the sleeve becomes a rule. It is a real, continuously enforced ceiling
    that shows up in the Rules UI and fires when breached.

    Idempotent by design: applying twice must re-level the existing cap, never
    stack a second one on the same ticker. It deliberately re-levels a cap the
    user set by hand too -- two max_weight rules on one ticker at two levels is
    the ambiguity this replaces -- and the return value says which it did, so
    the caller can tell them.
    """
    if strategy_catalog.get(strategy_id) is None:
        return []                       # static families are portfolios, not sleeves
    targets = sleeve_targets(strategy_id, sleeve_pct)
    if not targets:
        return []
    from sqlalchemy import select

    from app.models.tables import TradingRule
    name = (strategy_catalog.get(strategy_id) or {}).get("name", strategy_id)
    existing = {(r.ticker or "").upper(): r for r in (await session.scalars(
        select(TradingRule).where(TradingRule.subject == user.email,
                                  TradingRule.rule_type == "max_weight"))).all()}
    out: list[dict] = []
    for tk, weight in sorted(targets.items()):
        tk = tk.upper()
        level = round(float(weight) * 100.0, 1)
        if level <= 0:
            # A 0% cap is breached by any holding at all, so it would fire the
            # instant it is armed. "I want none of this" is a decision to act on,
            # not a rule to spring on someone.
            continue
        note = (f"{name}: the sleeve you chose. Caps {tk} at {level:g}% of the book "
                f"-- it does not make the rebalancer aim for it.")
        row = existing.get(tk)
        if row is not None:
            was = float(row.level)
            row.level = float(level)
            row.note = note
            row.active = True
            row.triggered = False
            out.append({"ticker": tk, "level": level, "previous_level": was,
                        "action": "relevelled" if was != level else "unchanged"})
        else:
            session.add(TradingRule(subject=user.email, ticker=tk, rule_type="max_weight",
                                    mode="pct", level=float(level), note=note))
            out.append({"ticker": tk, "level": level, "previous_level": None,
                        "action": "armed"})
    await session.commit()
    return out


async def apply_strategy(session: AsyncSession, user: User, strategy_id: str,
                         sleeve_pct: float | None = None) -> dict:
    # Either catalog: static baskets live in `strategies`, rule-based ones in
    # `strategy_catalog`. Adapting rather than forking keeps one apply path.
    s = cat.get(strategy_id) or strategy_catalog.as_legacy_strategy(strategy_id, sleeve_pct)
    if not s:
        return {"ok": False, "error": "unknown strategy"}
    # preset the plan. The sleeve is remembered so a later reload sizes the same
    # way -- applying at 20% and reloading at 100% would silently quadruple the
    # exposure the user chose.
    await upsert_plan(session, user, objective=s["objective"], risk_tolerance=s["risk_tolerance"],
                      preferred_depth=s.get("preferred_depth"), strategy=strategy_id,
                      strategy_sleeve_pct=s.get("sleeve_pct"))
    await session.commit()
    # The sleeve stops being decorative here: it becomes an enforced ceiling.
    caps = await _arm_sleeve_cap(session, user, strategy_id, s.get("sleeve_pct"))
    # rebalance trades toward the strategy's target allocation
    rows = await list_positions(session, user)
    nav = _nav(rows)
    actions = []
    if nav > 0:
        mix, _ = current_mix(rows)
        report = AllocationEngine().compute(target_allocation=s["target_allocation"],
                                            current_allocation=mix, nav=nav)
        actions = [a.model_dump() for a in report.rebalance_actions]
    return {"ok": True, "strategy": s, "nav": round(nav, 2), "rebalance_actions": actions,
            "sleeve_caps": caps,
            # Said out loud because it is the honest limit of option (a): the cap
            # stops the sleeve growing past the chosen size, it does not grow the
            # book INTO it. Funding is what does that.
            "sleeve_cap_note": (
                "This caps the sleeve at the size you chose; it does not make the "
                "rebalancer aim for it. Use 'Fund this sleeve' to grow into it."
            ) if caps else None}


# --------------------------------------------------------------------------- #
# Which tickers ARE the sleeve
# --------------------------------------------------------------------------- #
def sleeve_targets(strategy_id: str, sleeve_pct: float | None = None) -> dict[str, float]:
    """Target weight of NAV per ticker for the part that must actually be bought.

    For a rule-based strategy the sleeve is the *aggressive* leg — the core
    (``base``) is what the rest of the book already is, so funding it would mean
    selling your holdings to buy an index fund you never asked for. For a static
    basket every leg is the target, because there the basket IS the portfolio.
    """
    entry = strategy_catalog.get(strategy_id)
    if entry is not None:
        weights = entry.get("weights") or {}
        base = entry.get("base") or {}
        aggressive = {tk: w for tk, w in weights.items() if tk not in base}
        if not aggressive:                       # nothing to fund beyond the core
            return {}
        pct = sleeve_pct if sleeve_pct is not None else entry.get("sleeve_pct")
        frac = 1.0 if pct is None else max(0.0, min(1.0, float(pct) / 100.0))
        total_w = sum(aggressive.values()) or 1.0
        return {tk: (w / total_w) * frac for tk, w in aggressive.items()}
    s = cat.get(strategy_id)
    if not s:
        return {}
    return {tk: float(w) for tk, w in (s.get("basket") or [])}


def _held_ils(rows, snap) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in rows:
        tk = (p.ticker or "").upper()
        if is_cash_position(tk, p.meta if isinstance(p.meta, dict) else None):
            continue
        meta = p.meta if isinstance(p.meta, dict) else {}
        rate = fx_rate(price_currency(p.market, meta))
        out[tk] = out.get(tk, 0.0) + float(p.quantity) * float(p.current_price or 0.0) * rate
    return out


def _default_mode(strategy_id: str) -> str:
    # Rule-based strategies are sleeves; static families are model portfolios.
    return FUND if strategy_catalog.get(strategy_id) is not None else REPLACE


# --------------------------------------------------------------------------- #
# load_basket
# --------------------------------------------------------------------------- #
async def load_basket(session: AsyncSession, user: User, strategy_id: str,
                      total: float | None = None, sleeve_pct: float | None = None,
                      mode: str | None = None, dry_run: bool = False) -> dict:
    if sleeve_pct is None:
        _plan = await get_plan(session, user)
        if _plan is not None and getattr(_plan, "strategy", None) == strategy_id:
            sleeve_pct = getattr(_plan, "strategy_sleeve_pct", None)
    s = cat.get(strategy_id) or strategy_catalog.as_legacy_strategy(strategy_id, sleeve_pct)
    if not s:
        return {"ok": False, "error": "unknown strategy"}
    mode = (mode or _default_mode(strategy_id)).lower()
    if mode not in (FUND, REPLACE):
        return {"ok": False, "error": f"unknown mode '{mode}'"}
    if mode == FUND:
        return await _fund_sleeve(session, user, strategy_id, s, total=total,
                                  sleeve_pct=sleeve_pct, dry_run=dry_run)
    return await _replace_book(session, user, strategy_id, s, total=total, dry_run=dry_run)


# --------------------------------------------------------------------------- #
# mode="fund"
# --------------------------------------------------------------------------- #
async def _fund_sleeve(session: AsyncSession, user: User, strategy_id: str, s: dict,
                       *, total: float | None, sleeve_pct: float | None,
                       dry_run: bool) -> dict:
    targets = sleeve_targets(strategy_id, sleeve_pct)
    if not targets:
        return {"ok": False, "mode": FUND, "strategy_id": strategy_id,
                "error": "this strategy has no sleeve to fund — its basket is the core itself"}

    rows = await list_positions(session, user)
    snap = _snapshot(rows)
    nav = snap.get("nav") or 0.0
    if nav <= 0:
        return {"ok": False, "mode": FUND, "strategy_id": strategy_id,
                "error": "no tracked holdings to size a sleeve against — add holdings or cash first"}
    plan = await get_plan(session, user)
    objective = plan.objective if plan else "Grow"
    cap = effective_caps(plan)["concentration_cap"]
    cash_ils = await get_cash(session, user)
    held = _held_ils(rows, snap)

    # A `total` override means "spend exactly this much", split across the sleeve
    # by its own weights. Otherwise the sleeve is sized as a share of NAV and we
    # buy only the shortfall against what is already held.
    legs: list[dict] = []
    weight_sum = sum(targets.values()) or 1.0
    for tk, w in sorted(targets.items()):
        tk = tk.upper()
        if total and total > 0:
            want = float(total) * (w / weight_sum)
            short = want
        else:
            want = nav * w
            short = want - held.get(tk, 0.0)
        if short < MIN_TRADE_ILS:
            continue
        legs.append({"ticker": tk, "target_ils": round(want, 2),
                     "held_ils": round(held.get(tk, 0.0), 2),
                     "buy_ils": round(short, 2),
                     "target_pct": round(w * 100, 1)})
    amount = round(sum(x["buy_ils"] for x in legs), 2)
    if amount < MIN_TRADE_ILS:
        return {"ok": True, "mode": FUND, "strategy_id": strategy_id, "nav": round(nav, 2),
                "nothing_to_do": True, "buys": [], "funding": None,
                "message": (f"Your book already holds the sleeve at roughly its "
                            f"{', '.join(f'{k} {v * 100:.0f}%' for k, v in sorted(targets.items()))} "
                            f"target — nothing to buy.")}

    fund = plan_funding(rows, snap, plan, objective, cap, amount,
                        cash_ils=cash_ils, exclude=set(targets))

    # Abstain rather than half-execute: a partially funded sleeve is a position
    # nobody chose, at a size nobody chose. Judged in points of NAV, not shekels
    # -- see SLEEVE_SHORTFALL_TOLERANCE_PCT.
    held_sleeve = sum(held.get(tk.upper(), 0.0) for tk in targets)
    wanted_ils = held_sleeve + amount
    achievable_ils = held_sleeve + float(fund.get("funded_ils") or 0.0)
    chosen_pct = wanted_ils / nav * 100.0
    achievable_pct = achievable_ils / nav * 100.0
    short_points = chosen_pct - achievable_pct
    if short_points >= SLEEVE_SHORTFALL_TOLERANCE_PCT:
        return {"ok": False, "mode": FUND, "strategy_id": strategy_id, "nav": round(nav, 2),
                "error": "not enough fundable money for this sleeve",
                # Actionable, not just "no": it names the sleeve that WOULD work.
                "reason": (f"You asked for a {chosen_pct:.0f}% sleeve (₪{wanted_ils:,.0f}). "
                           f"The most this book can fund right now is {achievable_pct:.0f}% "
                           f"— ₪{fund['shortfall_ils']:,.0f} short, after spending "
                           f"₪{fund['from_cash_ils']:,.0f} of cash above your "
                           f"{fund['cash_floor_pct']:.0%} {objective} floor and trimming what "
                           f"the plan says is overweight. Lower the sleeve to about "
                           f"{achievable_pct:.0f}%, or add cash."),
                "chosen_sleeve_pct": round(chosen_pct, 1),
                "achievable_sleeve_pct": round(achievable_pct, 1),
                "buys": legs, "funding": fund,
                "funding_summary": describe_funding(fund)}

    preview = {"ok": True, "mode": FUND, "strategy_id": strategy_id, "nav": round(nav, 2),
               "sleeve_pct": sleeve_pct, "amount_ils": amount,
               "chosen_sleeve_pct": round(chosen_pct, 1),
               "achievable_sleeve_pct": round(achievable_pct, 1),
               "buys": legs, "funding": fund,
               "funding_summary": describe_funding(fund),
               "broker_note": BROKER_NOTE}
    if dry_run:
        return {**preview, "dry_run": True}

    executed = await _execute_funded_sleeve(session, user, legs, fund)
    return {**preview, "dry_run": False, **executed}


async def _execute_funded_sleeve(session: AsyncSession, user: User,
                                 legs: list[dict], fund: dict) -> dict:
    """Sell the named legs, then buy the sleeve. Nothing else is touched."""
    rows = await list_positions(session, user)
    by_ticker = {(p.ticker or "").upper(): p for p in rows}
    sold, raised, tax_total = [], 0.0, 0.0
    for leg in fund.get("sells") or []:
        p = by_ticker.get((leg.get("ticker") or "").upper())
        if p is None:
            continue
        shares = min(float(leg.get("shares") or 0), float(p.quantity))
        if shares <= 0:
            continue
        from app.core.config import get_settings
        rate = fx_rate(price_currency(p.market, p.meta if isinstance(p.meta, dict) else {}))
        price, basis = float(p.current_price or 0), float(p.cost_basis or 0)
        gross = shares * price * rate
        tax = max(0.0, (price - basis) * shares * rate) * float(get_settings().cgt_rate)
        remaining = float(p.quantity) - shares
        if remaining <= 0:
            await delete_position(session, user, p.ticker, p.market)
        else:
            p.quantity = Decimal(str(round(remaining, 6)))
        raised += max(0.0, gross - tax)
        tax_total += tax
        sold.append({"ticker": p.ticker, "shares": round(shares, 6),
                     "value_ils": round(gross, 2), "tax_ils": round(tax, 2),
                     "reason": leg.get("reason")})
    await session.flush()

    budget = round(float(fund.get("from_cash_ils") or 0.0) + raised, 2)
    entity = await ensure_entity(session, user, "Personal", "Personal")
    account = await ensure_account(session, entity, "Main")
    rows = await list_positions(session, user)
    by_ticker = {(p.ticker or "").upper(): p for p in rows}

    bought, skipped = [], []
    for leg in legs:
        tk, want = leg["ticker"], float(leg["buy_ils"])
        spend = min(want, budget)
        if spend < MIN_TRADE_ILS:
            skipped.append({"ticker": tk, "reason": "not enough funded money left"})
            continue
        try:
            q = guarded_quote(tk)
        except Exception:  # noqa: BLE001
            q = None
        price = float(getattr(q, "price", 0) or 0) if q is not None else 0.0
        if price <= 0:
            skipped.append({"ticker": tk, "reason": "no live price"})
            continue
        ccy = (getattr(q, "currency", None) or "USD")
        price_ils = price * (fx_rate(ccy) or 1.0)
        if price_ils <= 0:
            skipped.append({"ticker": tk, "reason": "no live price"})
            continue
        shares = spend / price_ils
        existing = by_ticker.get(tk)
        if existing is not None:
            # Blend the basis so gain/loss stays honest after topping up.
            old_qty, old_basis = float(existing.quantity), float(existing.cost_basis or 0)
            new_qty = old_qty + shares
            existing.quantity = Decimal(str(round(new_qty, 6)))
            if new_qty > 0:
                existing.cost_basis = Decimal(str(round(
                    (old_qty * old_basis + shares * price) / new_qty, 6)))
            existing.current_price = Decimal(str(price))
        else:
            mk = getattr(q, "market", None)
            mk = mk if mk in {m.value for m in Market} else "NASDAQ"
            await upsert_positions(session, account, [IntakePosition(
                ticker=tk, market=Market(mk), depth=3, spot_price=price,
                listing_price=price, quantity=shares, cost_basis=price,
                asset_class="Equities")])
        budget = round(budget - spend, 2)
        bought.append({"ticker": tk, "shares": round(shares, 6),
                       "amount_ils": round(spend, 2), "price": price,
                       "new_position": existing is None})
    await session.flush()

    # Cash pays only its own share; the sale proceeds went straight into the buy.
    # Anything left unspent (an unpriceable leg) is credited back rather than
    # evaporating -- money that vanishes is the bug class this whole phase exists
    # to remove.
    leftover = round(max(0.0, budget), 2)
    from_cash = round(float(fund.get("from_cash_ils") or 0.0), 2)
    cash_now = await get_cash(session, user)
    await set_cash(session, user, max(0.0, round(cash_now - from_cash + leftover, 2)))
    await session.commit()
    return {"sold": sold, "bought": bought, "skipped": skipped,
            "tax_ils": round(tax_total, 2), "unspent_ils": leftover,
            "cash_ils": round(max(0.0, cash_now - from_cash + leftover), 2)}


# --------------------------------------------------------------------------- #
# mode="replace"
# --------------------------------------------------------------------------- #
async def _replace_book(session: AsyncSession, user: User, strategy_id: str, s: dict,
                        *, total: float | None, dry_run: bool) -> dict:
    rows = await list_positions(session, user)
    snap = _snapshot(rows)
    budget = total if (total and total > 0) else ((snap.get("nav") or 0.0) or 10000.0)

    # What replacement would destroy, named and valued, so a confirm dialog can
    # show it rather than saying "your current holdings".
    held = _held_ils(rows, snap)
    removing = sorted(({"ticker": tk, "value_ils": round(v, 2)} for tk, v in held.items()),
                      key=lambda x: -x["value_ils"])

    # price the basket, then size by weight / price
    positions: list[IntakePosition] = []
    priced = []
    for ticker, weight in s["basket"]:
        try:
            price = float(guarded_quote(ticker).price)
        except Exception:  # noqa: BLE001
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
        return {"ok": False, "mode": REPLACE, "error": "could not price the basket"}

    preview = {"ok": True, "mode": REPLACE, "strategy_id": strategy_id,
               "budget": round(budget, 2), "loaded": priced, "count": len(priced),
               "removing": removing,
               "removing_value_ils": round(sum(x["value_ils"] for x in removing), 2),
               "broker_note": BROKER_NOTE}
    if dry_run:
        return {**preview, "dry_run": True}

    # full replace: delete existing holdings, then insert the basket
    for p in await list_positions(session, user):
        await session.delete(p)
    await session.flush()
    entity = await ensure_entity(session, user, "Personal", "Personal")
    account = await ensure_account(session, entity, "Main")
    await upsert_positions(session, account, positions)
    await session.commit()
    return {**preview, "dry_run": False}
