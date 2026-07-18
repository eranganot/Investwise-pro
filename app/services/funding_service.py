"""Where the money for a buy comes from, and how big the trade should be.

Every card used to say *what* to do without saying *how to pay for it* or *how
much*, which left the user to work out the sizing and the funding themselves --
so most cards read as advice rather than actions.

Two jobs:

* **Sizing** -- how much of a name to buy, bounded by the plan's target weight
  for its asset class and its single-name concentration cap. Never a round
  number pulled from nowhere.
* **Funding** -- cash first (down to a plan-derived floor), then the
  worst-fitting holdings, ranked by how badly they sit against the plan rather
  than by whatever is easiest to sell.

The cash floor is a percentage of NAV that varies by objective: a Preserve book
keeps more dry powder than a Grow book.
"""
from __future__ import annotations

import logging

from app.services.allocation_mix import OBJ_TARGET, classify

logger = logging.getLogger(__name__)

# Share of NAV kept liquid, by objective. Percentage rather than a fixed sum so
# the floor scales with the portfolio; overridable per plan.
CASH_FLOOR_PCT = {"Preserve": 0.10, "Income": 0.07, "Balanced": 0.05, "Grow": 0.03}
_DEFAULT_FLOOR = 0.05

# Don't propose a trade too small to be worth the friction.
MIN_TRADE_ILS = 250.0


def cash_floor_pct(objective: str | None, plan=None) -> float:
    override = getattr(plan, "cash_floor_pct", None) if plan is not None else None
    if override is not None:
        try:
            return max(0.0, min(0.5, float(override)))
        except (TypeError, ValueError):
            pass
    return CASH_FLOOR_PCT.get(objective or "Balanced", _DEFAULT_FLOOR)


def cash_floor_ils(nav: float, objective: str | None, plan=None) -> float:
    return max(0.0, float(nav or 0.0)) * cash_floor_pct(objective, plan)


def spendable_cash(cash_ils: float, nav: float, objective: str | None, plan=None) -> float:
    """Cash above the floor — what a purchase may actually draw on."""
    return max(0.0, float(cash_ils or 0.0) - cash_floor_ils(nav, objective, plan))


def size_purchase(nav: float, current_weight: float, target_weight: float,
                  cap: float | None = None) -> float:
    """ILS to buy to close the gap to target, clipped at the concentration cap."""
    if not nav or target_weight is None:
        return 0.0
    gap = max(0.0, float(target_weight) - float(current_weight or 0.0))
    if cap is not None:
        gap = min(gap, max(0.0, float(cap) - float(current_weight or 0.0)))
    return round(nav * gap, 2)


def _position_rows(rows, snap) -> list[dict]:
    from app.services.fx import fx_rate, price_currency
    nav = snap.get("nav") or 0.0
    out = []
    for p in rows or []:
        tk = (p.ticker or "").upper()
        if tk == "CASH":
            continue
        meta = p.meta if isinstance(p.meta, dict) else {}
        rate = fx_rate(price_currency(p.market, meta))
        price = float(p.current_price or 0.0)
        value = float(p.quantity) * price * rate
        out.append({
            "ticker": tk, "market": p.market, "price": price, "price_ils": price * rate,
            "quantity": float(p.quantity), "cost_basis": float(p.cost_basis or 0.0),
            "value_ils": value, "weight": (value / nav) if nav else 0.0,
            "asset_class": classify(tk, p.market, meta.get("asset_class")),
            "meta": meta, "_row": p,
        })
    return out


def rank_trim_candidates(rows, snap, objective: str | None, cap: float,
                         exclude: set[str] | None = None) -> list[dict]:
    """Holdings ranked by how poorly they fit the plan — worst fit sells first.

    Ordering is deliberate: sell what the plan says you're carrying too much of,
    not whatever happens to be up the most. Each candidate carries the reason so
    the card can explain itself.
    """
    exclude = {t.upper() for t in (exclude or set())}
    nav = snap.get("nav") or 0.0
    if not nav:
        return []
    target = OBJ_TARGET.get(objective or "Balanced", OBJ_TARGET["Balanced"])
    mix: dict[str, float] = {}
    positions = _position_rows(rows, snap)
    for p in positions:
        mix[p["asset_class"]] = mix.get(p["asset_class"], 0.0) + p["weight"]

    out = []
    for p in positions:
        if p["ticker"] in exclude or p["value_ils"] < MIN_TRADE_ILS:
            continue
        cls = p["asset_class"]
        class_over = max(0.0, mix.get(cls, 0.0) - target.get(cls, 0.0))
        name_over = max(0.0, p["weight"] - cap)
        gain_pct = ((p["price"] - p["cost_basis"]) / p["cost_basis"] * 100.0
                    if p["cost_basis"] else 0.0)
        # Trimming a loser realizes a deductible loss; trimming a big winner
        # triggers CGT. Prefer the tax-cheaper sale, all else being equal.
        tax_friendliness = 1.0 if gain_pct < 0 else max(0.0, 1.0 - min(gain_pct, 100.0) / 100.0)
        score = (name_over * 400.0) + (class_over * 100.0) + (tax_friendliness * 10.0)
        if score <= 0:
            continue
        if name_over > 0:
            reason = (f"{p['ticker']} is {p['weight']:.0%} of the book, above your "
                      f"{cap:.0%} single-name cap")
        else:
            reason = (f"{cls} is {mix.get(cls, 0.0):.0%} against a {target.get(cls, 0.0):.0%} "
                      f"target, so it's the overweight sleeve")
        out.append({**p, "score": round(score, 2), "reason": reason,
                    "gain_pct": round(gain_pct, 1),
                    "trimmable_ils": round(max(name_over, class_over) * nav, 2)})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def plan_funding(rows, snap, plan, objective: str | None, cap: float,
                 amount_ils: float, *, cash_ils: float = 0.0,
                 exclude: set[str] | None = None) -> dict:
    """Work out how to pay for `amount_ils`: cash first, then worst-fit holdings.

    Returns the concrete plan — how much from cash, which holdings to trim and by
    how many shares, estimated tax, and any shortfall — so the card can state it
    outright instead of leaving the user to figure it out.
    """
    nav = snap.get("nav") or 0.0
    amount_ils = max(0.0, float(amount_ils or 0.0))
    avail = spendable_cash(cash_ils, nav, objective, plan)
    from_cash = min(avail, amount_ils)
    remaining = round(amount_ils - from_cash, 2)

    sells: list[dict] = []
    tax_total = 0.0
    if remaining >= MIN_TRADE_ILS:
        for cand in rank_trim_candidates(rows, snap, objective, cap, exclude):
            if remaining < MIN_TRADE_ILS:
                break
            take = min(remaining, cand["trimmable_ils"] or cand["value_ils"], cand["value_ils"])
            if take < MIN_TRADE_ILS:
                continue
            price_ils = cand["price_ils"] or 0.0
            shares = int(take / price_ils) if price_ils else 0
            if shares <= 0:
                continue
            value = shares * price_ils
            gain = max(0.0, (cand["price"] - cand["cost_basis"])) * shares
            from app.core.config import get_settings
            from app.services.fx import fx_rate, price_currency
            rate = fx_rate(price_currency(cand["market"], cand["meta"]))
            tax = gain * rate * float(get_settings().cgt_rate)
            sells.append({"ticker": cand["ticker"], "market": cand["market"], "shares": shares,
                          "value_ils": round(value, 2), "tax_ils": round(tax, 2),
                          "reason": cand["reason"], "gain_pct": cand["gain_pct"]})
            tax_total += tax
            remaining = round(remaining - value, 2)

    funded = round(from_cash + sum(s["value_ils"] for s in sells) - tax_total, 2)
    return {
        "amount_ils": round(amount_ils, 2),
        "from_cash_ils": round(from_cash, 2),
        "sells": sells,
        "tax_ils": round(tax_total, 2),
        "funded_ils": max(0.0, funded),
        "shortfall_ils": round(max(0.0, amount_ils - max(0.0, funded)), 2),
        "cash_floor_ils": round(cash_floor_ils(nav, objective, plan), 2),
        "cash_floor_pct": cash_floor_pct(objective, plan),
    }


def describe_funding(fund: dict) -> str:
    """One plain sentence naming the money's source — no jargon, no ambiguity."""
    bits = []
    if fund.get("from_cash_ils"):
        bits.append(f"₪{fund['from_cash_ils']:,.0f} from cash")
    for s in fund.get("sells", []):
        bits.append(f"₪{s['value_ils']:,.0f} by selling {s['shares']} {s['ticker']}")
    if not bits:
        return "You don't have spendable cash above your floor, and nothing is overweight enough to trim."
    line = "Fund it with " + " and ".join(bits) + "."
    if fund.get("tax_ils"):
        line += f" Estimated tax on the sale: ₪{fund['tax_ils']:,.0f}."
    if fund.get("shortfall_ils"):
        line += f" That still leaves ₪{fund['shortfall_ils']:,.0f} short."
    return line
