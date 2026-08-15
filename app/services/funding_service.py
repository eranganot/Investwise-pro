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
from dataclasses import dataclass, field

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


def class_gap_ils(nav: float, mix: dict, cls: str, class_target: float) -> float:
    """How much the PLAN wants added to an asset class. Zero once at or over target.

    This is the only source of "the plan wants more here". It takes a mix keyed
    by asset class on purpose: you cannot reach it with a single ticker's weight.
    """
    if not nav or not class_target:
        return 0.0
    gap = max(0.0, float(class_target) - float((mix or {}).get(cls, 0.0) or 0.0))
    return round(float(nav) * gap, 2)


def name_room_ils(nav: float, ticker_weight: float, cap: float) -> float:
    """How much of ONE NAME can be added before the concentration cap bites.

    A ceiling, never a statement of intent. Room under the cap is not a reason to
    buy — it is only the most you may buy once something else has given a reason.
    """
    if not nav or cap is None:
        return 0.0
    return round(float(nav) * max(0.0, float(cap) - float(ticker_weight or 0.0)), 2)


# `size_purchase(nav, current_weight, target_weight, cap)` used to do both jobs.
# Both middle arguments were floats named "...weight", so passing a TICKER's
# weight where an ASSET CLASS's target belonged type-checked, read fine, and
# shipped: a name at 0% was sized against the whole 80% equities target and
# clipped only by the 40% cap, producing an 8,170 "plan gap" that no plan ever
# asked for. Two names that cannot be swapped by accident is the fix.
# Guarded by tests/test_funding_service.py::test_size_purchase_is_gone.


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
            # This says why THIS position was picked to sell. It is NOT a claim
            # that selling it corrects the overweight.
            #
            # The old wording -- "so it's the overweight sleeve" -- read as a
            # rebalance justification. On the first real C3 preview it labelled
            # two sales that way and then put every shekel back into the same
            # asset class: the 97% it named did not move by a single point. It
            # also said "sleeve" meaning ASSET CLASS, on a screen where sleeve
            # now means a strategy sleeve.
            reason = (f"cheapest way to raise it: {cls} is the most overweight class "
                      f"({mix.get(cls, 0.0):.0%} against a {target.get(cls, 0.0):.0%} target) "
                      f"and {p['ticker']} is among its least-taxed positions")
        out.append({**p, "score": round(score, 2), "reason": reason,
                    "asset_class": cls,
                    "gain_pct": round(gain_pct, 1),
                    "trimmable_ils": round(max(name_over, class_over) * nav, 2)})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def plan_funding(rows, snap, plan, objective: str | None, cap: float,
                 amount_ils: float, *, cash_ils: float = 0.0,
                 exclude: set[str] | None = None,
                 reserved: dict | None = None, cash_used: float = 0.0) -> dict:
    """Work out how to pay for `amount_ils`: cash first, then worst-fit holdings.

    Returns the concrete plan — how much from cash, which holdings to trim and by
    how many shares, estimated tax, and any shortfall — so the card can state it
    outright instead of leaving the user to figure it out.
    """
    nav = snap.get("nav") or 0.0
    amount_ils = max(0.0, float(amount_ils or 0.0))
    # `reserved` / `cash_used` are what EARLIER cards in this same build have
    # already committed to selling and spending. Without them each card plans
    # against the whole book and three cards spend the same shares twice.
    avail = max(0.0, spendable_cash(cash_ils, nav, objective, plan) - float(cash_used or 0.0))
    from_cash = min(avail, amount_ils)

    # What still has to be raised, measured in money that can actually be SPENT.
    #
    # The loop used to draw down a *gross* target and subtract tax only at the
    # end, so a plan that raised exactly the asking price came back short by the
    # tax plus whatever was left under the minimum -- on every card, forever.
    # Live: a 4,708 buy raised 4,290 gross and reported "still leaves 430 short"
    # (418 unraised + 11 tax) while claiming to be a complete funding plan.
    remaining_net = round(amount_ils - from_cash, 2)

    sells: list[dict] = []
    tax_total = 0.0
    if remaining_net >= MIN_TRADE_ILS:
        from app.core.config import get_settings
        from app.services.fx import fx_rate, price_currency
        cgt = float(get_settings().cgt_rate)

        # How much each class may give up in total, spent down as the loop sells.
        #
        # `trimmable_ils` is per-candidate and computed from a mix that never
        # moves, so a class 40% over target licensed selling 40% of NAV out of
        # EVERY holding in it -- the same overweight authorising the same sale
        # three times. On a three-position book that is a plan to liquidate 90%
        # of it, which is what surfaced the moment funding started raising the
        # amount actually asked for. One budget per class, drawn down once.
        target = OBJ_TARGET.get(objective or "Balanced", OBJ_TARGET["Balanced"])
        class_mix: dict[str, float] = {}
        for p in _position_rows(rows, snap):
            class_mix[p["asset_class"]] = class_mix.get(p["asset_class"], 0.0) + p["weight"]
        class_budget = {c: max(0.0, w - target.get(c, 0.0)) * nav for c, w in class_mix.items()}
        # Is anything at all overweight? If not, every candidate falls back to
        # "sell what you have"; if so, only the overweight may be sold.
        book_has_budget = (sum(class_budget.values())
                           + sum(max(0.0, p["weight"] - cap) * nav
                                 for p in _position_rows(rows, snap))) >= MIN_TRADE_ILS

        for cand in rank_trim_candidates(rows, snap, objective, cap, exclude):
            if remaining_net < MIN_TRADE_ILS:
                break
            price_ils = cand["price_ils"] or 0.0
            if price_ils <= 0:
                continue
            cls = cand.get("asset_class")
            name_allow = max(0.0, cand["weight"] - cap) * nav
            allow = max(name_allow, class_budget.get(cls, 0.0))
            if allow >= MIN_TRADE_ILS:
                # Bounded by whichever authorised the sale: this NAME being over
                # its own cap is a per-position fact, so it does not consume the
                # class budget; the class overweight does.
                pass
            elif book_has_budget:
                # This position's class is at or under its target while something
                # else in the book is over. Selling it would take a class the plan
                # is already short of and make it shorter, to buy something else.
                #
                # `rank_trim_candidates` offers it anyway: its score carries a
                # tax_friendliness term that stays positive for an underweight
                # class, and a zero `trimmable_ils` used to fall through to the
                # whole-position fallback below. That is how a war-room buy came
                # to be funded partly by selling BND at 3% against a 10% target.
                continue
            else:
                # Nothing in the book is overweight at all: there is no budget to
                # prefer, so the whole-position fallback stands and the buy is
                # funded from wherever it can be.
                allow = cand["value_ils"]
            rate = fx_rate(price_currency(cand["market"], cand["meta"]))
            gain_per_share = max(0.0, cand["price"] - cand["cost_basis"])
            # Tax as a fraction of this candidate's proceeds, so the sale can be
            # grossed up to net the amount actually still needed.
            tax_frac = min(0.95, (gain_per_share * rate * cgt) / price_ils)
            want_gross = remaining_net / (1.0 - tax_frac)
            # Only the shares nobody else has claimed yet.
            avail_shares = float(cand["quantity"]) - float((reserved or {}).get(cand["ticker"], 0.0))
            if avail_shares <= 0:
                continue
            sellable_value = avail_shares * price_ils
            take = min(want_gross, allow, sellable_value)
            if take < MIN_TRADE_ILS:
                continue
            shares = int(take / price_ils)
            if shares <= 0:
                continue
            # Whole lots floor the raise, so the last share is often the only
            # thing standing between "funded" and a shortfall smaller than one
            # share. Take it when the position can spare it -- never beyond the
            # position itself.
            net_per_share = price_ils * (1.0 - tax_frac)
            if (shares * net_per_share < remaining_net
                    and (shares + 1) * price_ils <= sellable_value):
                shares += 1
            value = shares * price_ils
            tax = gain_per_share * shares * rate * cgt
            sells.append({"ticker": cand["ticker"], "market": cand["market"], "shares": shares,
                          "value_ils": round(value, 2), "tax_ils": round(tax, 2),
                          # Carried so a caller can tell whether the proceeds are
                          # going straight back into the class they came out of.
                          "asset_class": cand.get("asset_class"),
                          "reason": cand["reason"], "gain_pct": cand["gain_pct"]})
            tax_total += tax
            remaining_net = round(remaining_net - (value - tax), 2)
            if cls in class_budget:
                class_budget[cls] = max(0.0, class_budget[cls] - value)

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


def buying_class_of(legs) -> str | None:
    """The single asset class a set of buy legs lands in, or None if mixed.

    Lives here rather than in any one consumer: it is the argument
    ``describe_funding`` cannot do its job without, so both the sleeve path and
    the Today path have to reach the same answer. It used to live in
    ``strategy_service`` alone, which is exactly how Today ended up never
    computing it at all.
    """
    return _single_class({str((leg or {}).get("ticker") or "").upper() for leg in (legs or [])})


def _single_class(tickers: set[str]) -> str | None:
    classes = {classify(t, "NASDAQ", None) for t in tickers if t}
    return classes.pop() if len(classes) == 1 else None


def describe_funding(fund: dict, buying_class: str | None) -> str:
    """One plain sentence naming the money's source — no jargon, no ambiguity.

    ``buying_class`` is what the proceeds are being spent on, and it is
    **required**. It has no default on purpose: when it had one, the Today path
    simply never passed it, the honesty clause below silently never fired, and
    three cards shipped claiming to move Equities from 97% toward an 80% target
    while buying equities with equities. A missing argument is now a TypeError at
    the call site instead of a missing sentence in production prose. ``None`` is
    still a legal *value* — a mixed-class buy makes no claim rather than a vague
    one — but you have to say so.
    """
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

    # The honesty clause. Selling equities to buy equities is a legitimate
    # trade -- swapping international and dividend exposure for factor exposure
    # is a real decision -- but it is NOT the rebalance the per-sale reasons
    # sound like, and the user has to be told which one they are agreeing to.
    sold = {s.get("asset_class") for s in fund.get("sells", []) if s.get("asset_class")}
    if buying_class and sold and sold == {buying_class}:
        line += (f" This does not change your {buying_class} weight — the proceeds buy "
                 f"{buying_class} again. It swaps which {buying_class.lower()} you hold.")

    if fund.get("shortfall_ils"):
        line += f" That still leaves ₪{fund['shortfall_ils']:,.0f} short."
    return line


# --------------------------------------------------------------------------- #
# The only way to build a funded buy
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FundedBuy:
    """A funded purchase whose narration is derived from its own arithmetic.

    Every field a card needs is computed here, together, from one simulation --
    so the prose and the numbers cannot drift apart. They drifted apart for
    months because sizing lived in one module, funding in a second, and the
    sentence describing both in a third.
    """
    ticker: str
    buying_class: str
    amount_ils: float
    fund: dict
    class_before: float
    class_after: float
    class_target: float
    kind: str                 # "toward_target" | "within_class_swap"
    summary: str
    impact: str
    apply_spec: dict

    @property
    def is_swap(self) -> bool:
        return self.kind == "within_class_swap"


# Below this, a move is lot-granularity noise rather than a change in the mix.
# A card claiming to move a weight must move it by at least this much.
MIN_CLAIMABLE_MOVE = 0.005


def propose_funded_buy(*, rows, snap, plan, objective: str | None, cap: float,
                       ticker: str, buying_class: str, cash_ils: float = 0.0,
                       requested_ils: float | None = None,
                       exclude: set[str] | None = None,
                       market: str = "NYSE",
                       allow_within_class_swap: bool = False,
                       ledger: "FundingLedger | None" = None) -> FundedBuy | None:
    """Size, fund, simulate and narrate a purchase — or return None.

    Returning None is the point as often as returning a card is. A purchase the
    plan does not want, cannot pay for, or that would push a class further from
    target is not a recommendation, and the honest thing to do with it is not to
    render it more carefully -- it is not to render it.

    ``allow_within_class_swap`` lets a caller keep a purchase whose funding comes
    entirely out of the class it buys back into. That trade is legitimate --
    shedding 3x leveraged exposure for factor exposure genuinely de-risks a book
    -- but it moves no weight, so it is *forced* to narrate as a swap. The caller
    opts into the trade, never into the label.
    """
    nav = float((snap or {}).get("nav") or 0.0)
    tk = (ticker or "").upper()
    if not nav or not tk or not buying_class:
        return None

    positions = _position_rows(rows, snap)
    value_by_class: dict[str, float] = {}
    for p in positions:
        value_by_class[p["asset_class"]] = value_by_class.get(p["asset_class"], 0.0) + p["value_ils"]
    invested = sum(value_by_class.values())
    if invested <= 0:
        return None
    mix = {c: v / invested for c, v in value_by_class.items()}
    ticker_weight = sum(p["weight"] for p in positions if p["ticker"] == tk)

    targets = OBJ_TARGET.get(objective or "Balanced", OBJ_TARGET["Balanced"])
    class_target = float(targets.get(buying_class, 0.0) or 0.0)

    # What the PLAN wants here, and separately the most this NAME may take.
    gap = class_gap_ils(nav, mix, buying_class, class_target)
    room = name_room_ils(nav, ticker_weight, cap)
    want = min(gap, room) if gap >= MIN_TRADE_ILS else room
    if gap < MIN_TRADE_ILS and not allow_within_class_swap:
        # The class is at or over target. Absent an explicit opt-in there is no
        # honest card here: buying more of a class the plan already holds too
        # much of cannot be a move toward the plan.
        return None
    if requested_ils is not None:
        want = min(want, max(0.0, float(requested_ils)))
    if want < MIN_TRADE_ILS:
        return None

    ex = {t.upper() for t in (exclude or set())} | {tk}
    _res = dict(ledger.shares) if ledger is not None else None
    _used = ledger.cash_ils if ledger is not None else 0.0
    fund = plan_funding(rows, snap, plan, objective, cap, want, cash_ils=cash_ils,
                        exclude=ex, reserved=_res, cash_used=_used)
    amount = min(want, float(fund.get("funded_ils") or 0.0))
    if amount < MIN_TRADE_ILS:
        return None
    if amount < want:
        # Re-plan at the size the book can actually pay for, so the funding legs
        # describe the trade being proposed rather than one that was rejected.
        fund = plan_funding(rows, snap, plan, objective, cap, amount,
                            cash_ils=cash_ils, exclude=ex, reserved=_res, cash_used=_used)
        amount = min(amount, float(fund.get("funded_ils") or 0.0))
        if amount < MIN_TRADE_ILS:
            return None

    # Simulate. This -- not the trade size, not the caller's intent -- is what
    # every claim below is rendered from.
    after = dict(value_by_class)
    after[buying_class] = after.get(buying_class, 0.0) + amount
    for s in fund.get("sells", []):
        cls = s.get("asset_class")
        if cls:
            after[cls] = after.get(cls, 0.0) - float(s.get("value_ils") or 0.0)
    invested_after = sum(after.values())
    if invested_after <= 0:
        return None
    class_before = mix.get(buying_class, 0.0)
    class_after = after.get(buying_class, 0.0) / invested_after

    moved = class_after - class_before
    wanted_direction = class_target - class_before
    if abs(moved) < MIN_CLAIMABLE_MOVE:
        kind = "within_class_swap"
    elif moved * wanted_direction > 0:
        kind = "toward_target"
    else:
        # Further from target than it started. There is no wording that makes
        # this a recommendation, so there is no card.
        return None
    if kind == "within_class_swap" and not allow_within_class_swap:
        return None

    # The buy can be right about its OWN class and still leave the book worse
    # off, because the money came out of a class that was already short.
    #
    # Found by the drift invariant in tests/test_card_claims.py: on a book 55%
    # equities against an 80% target, the commodities card closed the 10-point
    # commodities gap by selling equities, taking total drift from 0.70 to 1.27.
    # `rank_trim_candidates` allowed it because its score carries a
    # tax_friendliness term that stays positive when a class is UNDERweight, and
    # a class with no overweight budget falls through to the whole-position
    # fallback. Checking the buying class alone cannot catch that; checking the
    # whole mix can, and does so for every funding shape at once.
    #
    # A labelled swap is exempt: it moves no weight, so it cannot move drift.
    if kind != "within_class_swap":
        classes = set(targets) | set(value_by_class) | set(after)
        before_drift = sum(abs((value_by_class.get(c, 0.0) / invested)
                               - targets.get(c, 0.0)) for c in classes)
        after_drift = sum(abs((after.get(c, 0.0) / invested_after)
                              - targets.get(c, 0.0)) for c in classes)
        if after_drift > before_drift + 1e-9:
            return None

    if kind == "toward_target":
        impact = (f"Moves {buying_class} from {class_before:.0%} to {class_after:.0%} "
                  f"against your {class_target:.0%} target.")
    else:
        impact = (f"Does not move your {buying_class} weight ({class_before:.0%}). "
                  f"Swaps which {buying_class.lower()} you hold.")

    if ledger is not None:
        ledger.commit(fund)      # only once the card is definitely being returned
    return FundedBuy(
        ticker=tk, buying_class=buying_class, amount_ils=round(amount, 2), fund=fund,
        class_before=round(class_before, 4), class_after=round(class_after, 4),
        class_target=round(class_target, 4), kind=kind,
        summary=describe_funding(fund, buying_class), impact=impact,
        apply_spec={"kind": "buy_funded", "ticker": tk, "market": market,
                    "asset_class": buying_class, "amount_ils": round(amount, 2),
                    "from_cash_ils": fund.get("from_cash_ils", 0.0),
                    "sells": fund.get("sells", [])})


# --------------------------------------------------------------------------- #
# One inventory for the whole card set
# --------------------------------------------------------------------------- #
@dataclass
class FundingLedger:
    """What the cards built so far have already committed to spending.

    Threaded through every ``propose_funded_buy`` in one build, so each card is
    planned against what is LEFT rather than against the whole book. Three cards
    had each planned to sell the same 13 TQQQ and spend the same cash above the
    floor; money counted once by the broker and three times by the app.

    This replaces an earlier pass that let every card plan against the untouched
    book and then DROPPED the ones whose legs had been taken. That was worse than
    the bug it fixed: on the live book it silently removed the war-room card and
    the commodities card because a geo card had claimed 2 MSFT first -- while
    7,863 of V sat untouched and would have funded both. A card that can be paid
    for out of what remains must be re-sourced, not deleted.
    """
    shares: dict = field(default_factory=dict)
    cash_ils: float = 0.0

    def commit(self, fund: dict) -> None:
        self.cash_ils += float((fund or {}).get("from_cash_ils") or 0.0)
        for s in (fund or {}).get("sells", []):
            tk = str(s.get("ticker") or "").upper()
            self.shares[tk] = self.shares.get(tk, 0.0) + float(s.get("shares") or 0.0)
