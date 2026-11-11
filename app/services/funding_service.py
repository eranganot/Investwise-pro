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
import math
from dataclasses import dataclass, field

from app.services.allocation_mix import OBJ_TARGET, classify, classify_holding

logger = logging.getLogger(__name__)

# Share of NAV kept liquid, by objective. Percentage rather than a fixed sum so
# the floor scales with the portfolio; overridable per plan.
CASH_FLOOR_PCT = {"Preserve": 0.10, "Income": 0.07, "Balanced": 0.05, "Grow": 0.03}
_DEFAULT_FLOOR = 0.05

# Don't propose a trade too small to be worth the friction.
MIN_TRADE_DEFAULT_ILS = 250.0

# THE LINE NOTHING MAY CROSS, in shekels rather than a share of NAV.
#
# Eran, 2026-09-06: *"it should be a soft target... the hard minimum cash I
# should hold is 150."* A percentage floor scales with the book, which is right
# for a TARGET and wrong for a floor: the point of a hard minimum is that it
# does not move when the market does. 150 is his number, absolute, and it wins
# over the percentage whenever the percentage is smaller.
CASH_HARD_FLOOR_DEFAULT_ILS = 150.0


# PR 104d: both are settings now (plans.thresholds); the constants above are the
# defaults. `MIN_TRADE_ILS` / `CASH_HARD_FLOOR_ILS` still resolve, per call, for
# every `from funding_service import ...` (PEP 562 module __getattr__).
def min_trade_ils() -> float:
    from app.services import thresholds
    return float(thresholds.get("min_funded_trade_ils", MIN_TRADE_DEFAULT_ILS))


def cash_hard_floor_ils() -> float:
    from app.services import thresholds
    return float(thresholds.get("cash_floor_ils", CASH_HARD_FLOOR_DEFAULT_ILS))


def __getattr__(name):
    if name == "MIN_TRADE_ILS":
        return min_trade_ils()
    if name == "CASH_HARD_FLOOR_ILS":
        return cash_hard_floor_ils()
    raise AttributeError(name)


def cash_target_ils_of(plan) -> float | None:
    """The absolute cash target in shekels, or None when the plan uses a %."""
    raw = getattr(plan, "cash_target_ils", None) if plan is not None else None
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


def cash_target_fraction(objective: str | None, plan=None, nav: float | None = None) -> float:
    """The book's cash TARGET, as a share of NAV. A FRACTION.

    THE NAME IS OLDER THAN THE MEANING, and the meaning changed on 2026-09-06.
    This used to be a floor and its docstring argued for that at length:

        "THE FLOOR IS THE TARGET, and that is the point rather than a shortcut.
         A reserve the machine is allowed to nibble into is not a reserve: on
         the live book, `Grow` floors cash at 3% and the balance sat at 2.92%,
         so the app had already spent the reserve down to its own limit and
         nothing on any screen called that a decision."

    That reasoning still holds against the failure it was written for -- routine
    cards quietly eating the reserve -- and it is kept here rather than deleted,
    because the failure has not gone away. What Eran changed is narrower:

        "it should be a soft target. meaning if there is a good/valueble/
         interesting holding that I must buy you can use the cash to do it -->
         but if you recommend me to use my cash for this investment you should
         explain why I should go below the 7.5%. the hard minimum cash I should
         hold is 150."

    So there are now TWO numbers where there was one. This function returns the
    SOFT one -- what the book is managed toward, and what every routine card
    still stops at. ``CASH_HARD_FLOOR_ILS`` is the other, and nothing crosses it.
    A card may spend between them only by naming a reason: ``plan_funding``
    refuses ``allow_reserve`` without a ``reserve_reason``, so "explain why" is
    a precondition in the code rather than a habit in the prose.

    PR 116 renamed it (it was ``cash_floor_pct``): it is the soft TARGET. The
    funding payload still carries ``cash_floor_ils`` / ``cash_floor_pct`` for
    the UI, beside ``cash_target_ils`` / ``cash_target_pct``; the old function
    names stay as deprecated aliases below.

    UNITS. `plans.cash_target_pct` is a PERCENTAGE (7.5), like every other
    `_pct` column on that table. This returns a FRACTION, like `CASH_FLOOR_PCT`
    and every caller here. The /100 happens once, here.

    This replaces a `plan.cash_floor_pct` lookup that had read as configurable
    for months and could never fire: `Plan` has never had that column, so
    `getattr` returned None on every call and the objective table was the only
    answer the function could give.
    """
    # A SHEKEL TARGET IS A SHARE OF TODAY'S BOOK. Converted here, with the NAV
    # the caller passes, so every percentage reader agrees with the shekel one.
    ils = cash_target_ils_of(plan)
    if ils is not None and nav:
        return max(0.0, min(0.5, ils / float(nav)))
    raw = getattr(plan, "cash_target_pct", None) if plan is not None else None
    if raw is not None:
        try:
            return max(0.0, min(0.5, float(raw) / 100.0))
        except (TypeError, ValueError):
            pass
    return CASH_FLOOR_PCT.get(objective or "Balanced", _DEFAULT_FLOOR)


def cash_target_amount_ils(nav: float, objective: str | None, plan=None) -> float:
    """The book's cash TARGET in shekels: the plan's shekel target, else the fraction x NAV."""
    ils = cash_target_ils_of(plan)
    if ils is not None:
        return ils
    return max(0.0, float(nav or 0.0)) * cash_target_fraction(objective, plan)


# Deprecated aliases (PR 116): the soft target was called a floor for months.
cash_floor_pct = cash_target_fraction
cash_floor_ils = cash_target_amount_ils


def cash_soft_floor_ils(nav: float, objective: str | None, plan=None) -> float:
    """The line routine funding stops at: the cash target, never below the hard floor.

    ``max`` of the two, not the percentage alone. On a small book 7.5% of NAV can
    be less than 150, and a target that dips under the hard minimum would let the
    ordinary path spend into it without anyone opting in.
    """
    return max(cash_target_amount_ils(nav, objective, plan), cash_hard_floor_ils())


def spendable_cash(cash_ils: float, nav: float, objective: str | None, plan=None,
                   *, allow_reserve: bool = False) -> float:
    """Cash a purchase may draw on.

    Default: down to the cash TARGET, which is what every routine card gets and
    is exactly the behaviour that existed before the target became soft.

    ``allow_reserve=True``: down to ``CASH_HARD_FLOOR_ILS`` instead. Reserved for
    a card that has a specific holding and a reason to name -- ``plan_funding``
    will not honour it without one.
    """
    floor = (cash_hard_floor_ils() if allow_reserve
             else cash_soft_floor_ils(nav, objective, plan))
    return max(0.0, float(cash_ils or 0.0) - floor)


def class_gap_ils(nav: float, mix: dict, cls: str, class_target: float) -> float:
    """How much the PLAN wants added to an asset class. Zero once at or over target.

    This is the only source of "the plan wants more here". It takes a mix keyed
    by asset class on purpose: you cannot reach it with a single ticker's weight.
    """
    if not nav or not class_target:
        return 0.0
    gap = max(0.0, float(class_target) - float((mix or {}).get(cls, 0.0) or 0.0))
    return round(float(nav) * gap, 2)


def effective_name_cap(cap: float, ticker: str, name_caps: dict | None = None) -> float:
    """The tighter of the plan-wide cap and this ticker's own ``max_weight`` rule.

    The tighter one, always. A hand-set cap is a limit the user chose for that
    position; the plan-wide one is a limit the objective implies for every
    position. Neither may be relaxed by the other, so the binding constraint is
    the minimum -- and a book with no rule on a ticker behaves exactly as before.
    """
    base = float(cap) if cap is not None else 1.0
    own = (name_caps or {}).get((ticker or "").upper())
    return min(base, float(own)) if own is not None else base


def name_room_ils(nav: float, ticker_weight: float, cap: float) -> float:
    """How much of ONE NAME can be added before the concentration cap bites.

    A ceiling, never a statement of intent. Room under the cap is not a reason to
    buy — it is only the most you may buy once something else has given a reason.

    ``cap`` is the EFFECTIVE cap for this ticker -- see `effective_name_cap`.
    Callers pass the plan-wide number only when the book has no rule on the name.
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
            "asset_class": classify_holding(p),
            "meta": meta, "_row": p,
        })
    return out


def targets_for(objective: str | None, book_target: dict | None = None) -> dict:
    """THE TARGET MIX THE REST OF THE APP USES, when the caller can supply it.

    `OBJ_TARGET` is the objective table: Grow is 80% Equities, and it knows
    nothing about sleeves or a core. The book's own target is sleeves plus core
    (`_book_target` in recommendations.py), and measuring drift against the wrong
    one of the two can invert its sign.

    Measured live on 2026-09-03: Equities at 86.25% is **6 points over** the
    objective table and **3 points under** the book target of 89.2% (73% sleeves
    + 16.2% core). `/mix` reported `rebalance_required: false` and a drift of
    -2.95%, while the funding layer, reading the objective table, told two cards
    that *"Equities is the most overweight class (86% against a 80% target)"*
    and funded both by selling VTI -- the core basket bought the day before.
    Two parts of the same screen, opposite signs, same book.

    MERGED, not replaced. The book target is authoritative for the classes it
    names and silent about the rest: sleeves plus core describe Equities and
    Fixed Income and say nothing at all about Commodities, because neither a
    60/40 core nor two equity sleeves has an opinion on it. Replacing the table
    outright would set the Commodities target to zero, and `propose_funded_buy`
    refuses a buy into a class with no gap -- so the two commodity idea cards on
    Today would have vanished, not because the plan rejected them but because
    the plan had never been asked. Overriding only the named keys fixes the sign
    of the Equities drift and leaves the objective's view of a class the book
    does not model.

    Callers that know the book target pass it. Paths with no session to resolve
    one keep the objective table unchanged.
    """
    # PR 71 -- REPLACED, no longer merged. The merge filled every class the book
    # target did not name from the objective table, and on 2026-09-22 that
    # summed to 110%: the book named Equities/Fixed Income/Cash, the Grow table
    # added Commodities 10%, "Consider USO" bought toward that 10%, and the
    # rebalance card -- reading the book target, where Commodities was 0% --
    # offered to sell it the same day. The reason for merging (a 60/40 core
    # has no opinion on Commodities, so replacing would zero the class) is now
    # answered where it belongs: `book_target.compose` carves the objective's
    # Commodities share out of the core, so the book target is complete and
    # sums to 1.0 by construction. One target, every reader.
    if book_target:
        return {k: float(v) for k, v in dict(book_target).items() if v is not None}
    return dict(OBJ_TARGET.get(objective or "Balanced", OBJ_TARGET["Balanced"]))


def rank_trim_candidates(rows, snap, objective: str | None, cap: float,
                         exclude: set[str] | None = None,
                         book_target: dict | None = None,
                         prefer: set[str] | None = None,
                         name_caps: dict | None = None,
                         name_targets: dict | None = None) -> list[dict]:
    """Holdings ranked by how poorly they fit the plan — worst fit sells first.

    Ordering is deliberate: sell what the plan says you're carrying too much of,
    not whatever happens to be up the most. Each candidate carries the reason so
    the card can explain itself.

    ``book_target`` is the sleeves-plus-core target; see `targets_for`. Without
    it this measures against the objective table, which on a book running sleeves
    is a different number and sometimes a different sign.

    ``prefer`` NAMES THE POSITION THE TRADE IS ACTUALLY CONVERTING, and it ranks
    ahead of everything.

    This ordering is about fit and tax, which is right when funding a purchase
    out of whatever the book can best spare. It is wrong when the purchase IS a
    conversion of one specific holding: funding a QQQ sleeve out of a book
    holding TQQQ, the ranking picked VTI ₪2,925 alongside TQQQ ₪4,145, because
    Equities was the most overweight class and VTI carried the smaller gain. Both
    sentences were true and the plan was still wrong — it sold core money doing
    its job to start a sleeve whose funding was sitting right there in the
    leveraged position the sleeve replaces. Measured on the live book, PR 61.1.

    A preferred candidate is never dropped for scoring zero, and carries its own
    reason, because "least-taxed position in the most overweight class" does not
    describe why it was chosen.

    ``name_targets`` (PR 164) is the plan's own weight for each NAME, as a
    fraction of the book. When it is given, a holding above its own target --
    and a holding the plan does not name at all, whose target is zero -- ranks
    ahead of every fit-and-tax term. Without it the ranking only knew classes,
    and inside one class it picked by tax alone. Measured 2026-11-10/11: the
    VXUS band card sold VTI (+0.8%) while XLE and XLF, which no part of the plan
    holds, sat unsold; the sale took VTI below its own band, the VTI band card
    sold VXUS (+0.3%) back, and the pair alternated 16 legs in 24 hours.
    """
    exclude = {t.upper() for t in (exclude or set())}
    prefer = {t.upper() for t in (prefer or set())} - exclude
    nav = snap.get("nav") or 0.0
    if not nav:
        return []
    target = targets_for(objective, book_target)
    mix: dict[str, float] = {}
    positions = _position_rows(rows, snap)
    for p in positions:
        mix[p["asset_class"]] = mix.get(p["asset_class"], 0.0) + p["weight"]

    out = []
    for p in positions:
        if p["ticker"] in exclude or p["value_ils"] < min_trade_ils():
            continue
        cls = p["asset_class"]
        class_over = max(0.0, mix.get(cls, 0.0) - target.get(cls, 0.0))
        # A POSITION IS OVER ITS OWN CAP BEFORE IT IS OVER THE PLAN'S. Measured
        # 2026-09-06: NVDA at 20% of the book against a hand-set 12% cap ranked
        # BELOW an overweight BND and carried the reason "corrects no drift",
        # because this measured `weight - concentration_cap` and the plan-wide
        # number was 40%. The cap the user set was not in the arithmetic at all.
        _cap_here = effective_name_cap(cap, p["ticker"], name_caps)
        name_over = max(0.0, p["weight"] - _cap_here)
        gain_pct = ((p["price"] - p["cost_basis"]) / p["cost_basis"] * 100.0
                    if p["cost_basis"] else 0.0)
        # Trimming a loser realizes a deductible loss; trimming a big winner
        # triggers CGT. Prefer the tax-cheaper sale, all else being equal.
        tax_friendliness = 1.0 if gain_pct < 0 else max(0.0, 1.0 - min(gain_pct, 100.0) / 100.0)
        score = (name_over * 400.0) + (class_over * 100.0) + (tax_friendliness * 10.0)
        # Over the plan's own number for this name. Tiered, not weighted: a name
        # 3.9% over its target must outrank a tax difference of 0.3 points, and
        # a weight of 400 on 0.039 would not (15.6 vs 10).
        plan_over = (max(0.0, p["weight"] - float(name_targets.get(p["ticker"], 0.0) or 0.0))
                     if name_targets is not None else 0.0)
        if plan_over * nav >= min_trade_ils():
            score += 1000.0 + plan_over * 400.0
        else:
            plan_over = 0.0
        preferred = p["ticker"] in prefer
        if preferred:
            # Above every fit-and-tax term by construction: name_over maxes out
            # around 400 and class_over around 100, so this cannot be outranked
            # by a book that happens to be very overweight somewhere else.
            score += 10_000.0
        elif score <= 0:
            continue
        if preferred:
            reason = (f"{p['ticker']} is the position this move converts — the sleeve "
                      f"you are funding is the index it was leveraging, so this is the "
                      f"trade itself rather than a way to pay for it")
        elif name_over > 0:
            _own = (name_caps or {}).get(p["ticker"])
            _whose = ("your own cap on it" if _own is not None and _own <= cap
                      else "your single-name cap")
            reason = (f"{p['ticker']} is {p['weight']:.0%} of the book, above "
                      f"{_whose} of {_cap_here:.0%}")
        elif plan_over > 0:
            _t = float(name_targets.get(p["ticker"], 0.0) or 0.0)
            reason = (f"{p['ticker']} is not part of your plan ({p['weight']:.1%} of the book)"
                      if _t <= 0 else
                      f"{p['ticker']} is {p['weight']:.1%} of the book, above your plan's "
                      f"{_t:.1%} for it")
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
            #
            # AND IT MAY ONLY SAY "OVERWEIGHT" WHEN THE CLASS IS OVER. This
            # sentence was unconditional, so it fired on the tax_friendliness
            # term alone. Measured live on 2026-09-03: two cards funded by
            # selling VTI carried the reason *"Equities is the most overweight
            # class (86% against a 80% target)"* while `/mix` was reporting
            # `rebalance_required: false` and Equities 3 points UNDER its 89.2%
            # book target. Reading the objective table was half of that; saying
            # "overweight" with `class_over` at zero is the other half, and it
            # survives the target fix on its own if left alone.
            #
            # And MATERIAL, by the app's own unit. A class over by less than
            # MIN_TRADE_ILS is not something a sale can correct -- there is no
            # trade that small -- and at display precision it prints as "Fixed
            # Income is the most overweight class (11% against a 11% target)",
            # a sentence naming an overweight of zero. Measured on the live book
            # once the book target was threaded in: BND was over by 0.06 points,
            # ILS 15.73.
            if class_over * nav >= min_trade_ils():
                reason = (f"cheapest way to raise it: {cls} is the most overweight class "
                          f"({mix.get(cls, 0.0):.0%} against a {target.get(cls, 0.0):.0%} target) "
                          f"and {p['ticker']} is among its least-taxed positions")
            else:
                reason = (f"cheapest way to raise it, but it corrects no drift: {cls} is at "
                          f"{mix.get(cls, 0.0):.0%} against a {target.get(cls, 0.0):.0%} "
                          f"target and {p['ticker']} is among its least-taxed positions. "
                          f"Selling it does not move you closer to your plan")
        out.append({**p, "score": round(score, 2), "reason": reason,
                    "asset_class": cls, "preferred": preferred,
                    "gain_pct": round(gain_pct, 1),
                    "plan_over": round(plan_over, 4),
                    "trimmable_ils": round(max(name_over, class_over, plan_over) * nav, 2)})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def plan_funding(rows, snap, plan, objective: str | None, cap: float,
                 amount_ils: float, *, cash_ils: float = 0.0,
                 exclude: set[str] | None = None,
                 reserved: dict | None = None, cash_used: float = 0.0,
                 buying_class: str | None = None,
                 book_target: dict | None = None,
                 prefer: set[str] | None = None,
                 allow_reserve: bool = False,
                 reserve_reason: str | None = None,
                 name_caps: dict | None = None,
                 swap_first: bool = False,
                 name_targets: dict | None = None,
                 name_floors: dict | None = None) -> dict:
    """Work out how to pay for `amount_ils`: cash first, then worst-fit holdings.

    ``name_targets`` / ``name_floors`` (PR 164, D147): the plan's weight for each
    name, and the lowest weight a sale may leave it at (a banded name's floor is
    the bottom of its band). Off-plan names rank first; no sale takes a name
    below its floor. Passed by the band card only -- see `rank_trim_candidates`.

    ``swap_first`` (PR 138) is for a SLEEVE top-up: every sale that stays inside
    the class being bought (a swap, which moves no weight) is taken before any
    sale from another class. Left to the fit-and-tax ranking, a book sitting on
    its class targets ranks a loss-making Commodities holding level with a
    loss-making Equities one, sells both to fund an Equities sleeve, and the
    drift gate then refuses the whole plan. Live 2026-10-22: GOOGL then UGA,
    refused, while XOM (Equities) sat unsold. Rank order inside each group is
    unchanged.

    Returns the concrete plan — how much from cash, which holdings to trim and by
    how many shares, estimated tax, and any shortfall — so the card can state it
    outright instead of leaving the user to figure it out.

    ``buying_class`` IS WHAT MAKES A 64% SLEEVE TARGET REACHABLE.

    Every sale here is gated by a class-overweight budget: a class may give up
    only what it holds above its target. That is exactly right when the proceeds
    LEAVE the class -- selling equities to buy bonds is a rebalance, and the
    budget IS the rebalance. It is the wrong constraint when the proceeds come
    straight back: selling V and SCHD to buy TQQQ is Equities to Equities, and
    **the class weight does not move at all**. There is nothing for a class
    budget to protect.

    That mistake was the whole of symptom #4. The sleeves target 64% and hold
    15%; filling them needs about 13,968, and Equities sat 17.8 points over
    target, so the budget authorised about 5,077. Both sleeves abstained, every
    time, while the Sleeves card went on printing 64% as though it were a fact.
    The instruction -- "if the sleeves are set to 64%, they should actually BE
    64%" -- was being overruled by a guard aimed at a different trade.

    So when a candidate's class equals ``buying_class``, the class budget stops
    gating it and the position becomes eligible on its own merits. What still
    binds: ``exclude`` (never fund one sleeve by selling another), ``reserved``
    (never sell the same share twice), the amount actually asked for, and tax,
    which is charged and reported as always.

    ``None`` preserves the old behaviour exactly, so nothing that has not opted
    in changes. Note that ``describe_funding`` has told the user for some time
    that a same-class swap does not move their weights -- prose describing a
    trade the funder would not authorise.
    """
    nav = snap.get("nav") or 0.0
    amount_ils = max(0.0, float(amount_ils or 0.0))

    # A REASON IS A PRECONDITION, NOT A CONVENTION. Eran's instruction was that
    # the cash target may be crossed for something worth buying "but if you
    # recommend me to use my cash for this investment you should explain why I
    # should go below the 7.5%". A boolean alone would let a future call site
    # opt in and forget the sentence, so the opt-in is refused without one --
    # the same shape as `describe_funding`'s required `buying_class`, and for
    # the same reason: a missing argument is better than a missing sentence.
    reserve_reason = (reserve_reason or "").strip() or None
    allow_reserve = bool(allow_reserve and reserve_reason)

    # `reserved` / `cash_used` are what EARLIER cards in this same build have
    # already committed to selling and spending. Without them each card plans
    # against the whole book and three cards spend the same shares twice.
    avail = max(0.0, spendable_cash(cash_ils, nav, objective, plan,
                                    allow_reserve=allow_reserve)
                - float(cash_used or 0.0))
    from_cash = min(avail, amount_ils)

    # How much of that came from BELOW the target, so the card can say it and
    # `describe_funding` can explain it. Measured against the balance this card
    # actually sees -- `cash_used` is what earlier cards in the same build have
    # already spoken for.
    _soft = cash_soft_floor_ils(nav, objective, plan)
    _before = max(0.0, float(cash_ils or 0.0) - float(cash_used or 0.0))
    _after = max(0.0, _before - from_cash)
    reserve_used = round(max(0.0, min(_soft, _before) - _after), 2)

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
    # How much of this plan is a same-class swap rather than a rebalance. Two
    # different trades wearing the same "sell X to buy Y" sentence, and the card
    # has to be able to tell the user which one it is proposing.
    swap_ils = 0.0
    if remaining_net >= min_trade_ils():
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
        target = targets_for(objective, book_target)
        class_mix: dict[str, float] = {}
        for p in _position_rows(rows, snap):
            class_mix[p["asset_class"]] = class_mix.get(p["asset_class"], 0.0) + p["weight"]
        class_budget = {c: max(0.0, w - target.get(c, 0.0)) * nav for c, w in class_mix.items()}
        # Is anything at all overweight? If not, every candidate falls back to
        # "sell what you have"; if so, only the overweight may be sold.
        book_has_budget = (sum(class_budget.values())
                           + sum(max(0.0, p["weight"] - cap) * nav
                                 for p in _position_rows(rows, snap))) >= min_trade_ils()

        _cands = rank_trim_candidates(rows, snap, objective, cap, exclude,
                                      book_target=book_target, prefer=prefer,
                                      name_caps=name_caps, name_targets=name_targets)
        if swap_first and buying_class:
            # sorted() is stable: inside each group the fit-and-tax order stands.
            _cands = sorted(_cands, key=lambda c: 0 if (c.get("preferred")
                                                        or c.get("asset_class") == buying_class) else 1)
        for cand in _cands:
            if remaining_net < min_trade_ils():
                break
            price_ils = cand["price_ils"] or 0.0
            if price_ils <= 0:
                continue
            cls = cand.get("asset_class")
            name_allow = max(0.0, cand["weight"]
                             - effective_name_cap(cap, cand["ticker"], name_caps)) * nav
            allow = max(name_allow, class_budget.get(cls, 0.0))
            # A preferred candidate is eligible on its own terms for the same
            # reason `buying_class` is: a step-back swap converts one Equities
            # position into another, so the class weight does not move and the
            # overweight budget has nothing to protect. Without this the
            # underweight-class `continue` below could drop the very position
            # the trade is about.
            same_class = (bool(buying_class) and cls == buying_class) or cand.get("preferred")
            if same_class:
                # The proceeds buy this class back, so the class weight is
                # unchanged and the overweight budget has nothing to protect.
                # The position is eligible on its own terms; `remaining_net`
                # still caps the total and `exclude` still protects sleeves.
                allow = max(allow, cand["value_ils"])
            if allow >= min_trade_ils():
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
            # A NAME IS NEVER SOLD BELOW ITS OWN FLOOR. The floor is a weight of
            # the book; what may go is what this position holds above it, less
            # anything an earlier card already claimed.
            _floor = (name_floors or {}).get(cand["ticker"])
            if _floor is not None:
                _held_after_claims = cand["value_ils"] - float(
                    (reserved or {}).get(cand["ticker"], 0.0)) * price_ils
                sellable_value = min(sellable_value,
                                     max(0.0, _held_after_claims - float(_floor) * nav))
            take = min(want_gross, allow, sellable_value)
            if take < min_trade_ils():
                continue

            # SELL FRACTIONS, BECAUSE THE BOOK HOLDS FRACTIONS.
            #
            # This was `shares = int(take / price_ils)` followed by
            # `if shares <= 0: continue`, and the combination cost a whole sleeve
            # on 2026-08-25. Two failures, one line apart:
            #
            #  * THE ZERO GUARD FIRED BEFORE THE ROUNDING CORRECTION UNDER IT.
            #    Funding needed 1,394.90 more and V costs 1,418.74 a share, so
            #    `int()` gave 0 and the position was skipped ENTIRELY -- 7,954 of
            #    sellable V passed over because one share was 24 shekels too big.
            #    The "take one more share" rule immediately below existed for
            #    exactly this and never got reached. The SOXL sleeve was reported
            #    1,357 short with V untouched in the book.
            #
            #  * WHOLE LOTS STRAND EVERY FRACTION. This book is entirely
            #    fractional -- V 5.607, MSFT 2.9358, SCHD 42.4748 -- so `int()`
            #    quietly wrote off 861 of V, 62 of SCHD, and ALL 1,692 of MSFT,
            #    which at 0.9358 shares could never sell a single share however
            #    much was needed.
            #
            # Whole lots were never a real constraint here. InvestWise places no
            # brokerage order (`BROKER_NOTE`), positions are stored as fractional
            # Decimals, and a book holding 5.607 V was bought fractionally in the
            # first place. Sizing the sale in shekels and converting once removes
            # both failures: no overshoot to correct, and nothing stranded.
            #
            # Floored to 4dp -- the stored precision -- so rounding can never ask
            # for more shares than the position holds.
            shares = math.floor((take / price_ils) * 10000.0) / 10000.0
            shares = min(shares, avail_shares)
            value = shares * price_ils
            if value < min_trade_ils():
                continue
            tax = gain_per_share * shares * rate * cgt
            sells.append({"ticker": cand["ticker"], "market": cand["market"], "shares": shares,
                          "value_ils": round(value, 2), "tax_ils": round(tax, 2),
                          # Carried so a caller can tell whether the proceeds are
                          # going straight back into the class they came out of.
                          "asset_class": cand.get("asset_class"),
                          "reason": cand["reason"], "gain_pct": cand["gain_pct"]})
            tax_total += tax
            remaining_net = round(remaining_net - (value - tax), 2)
            if same_class:
                swap_ils += value
            elif cls in class_budget:
                # A same-class sale must NOT draw down the class budget: it did
                # not change the class weight, so there is nothing to spend. Two
                # sleeve legs funded out of Equities would otherwise consume a
                # rebalancing allowance neither of them used.
                class_budget[cls] = max(0.0, class_budget[cls] - value)

    funded = round(from_cash + sum(s["value_ils"] for s in sells) - tax_total, 2)
    return {
        "amount_ils": round(amount_ils, 2),
        "from_cash_ils": round(from_cash, 2),
        "sells": sells,
        "tax_ils": round(tax_total, 2),
        "funded_ils": max(0.0, funded),
        "shortfall_ils": round(max(0.0, amount_ils - max(0.0, funded)), 2),
        # PR 116: the soft TARGET. `cash_floor_*` stay as deprecated aliases
        # for one release (the UI and older verdicts read them).
        "cash_target_ils": round(cash_target_amount_ils(nav, objective, plan), 2),
        "cash_target_pct": cash_target_fraction(objective, plan, nav),
        "cash_floor_ils": round(cash_target_amount_ils(nav, objective, plan), 2),
        "cash_floor_pct": cash_target_fraction(objective, plan, nav),
        # THE SOFT/HARD SPLIT, on the payload, because a card that spends into
        # the reserve has to be able to show the reader where it landed.
        "cash_hard_floor_ils": cash_hard_floor_ils(),
        "reserve_used_ils": reserve_used,
        "reserve_reason": reserve_reason if reserve_used else None,
        "cash_after_ils": round(_after, 2),
        "cash_after_pct": round(_after / nav, 4) if nav else 0.0,
        # Named, so the card can say which trade this is and how much of the
        # book it turns over. A 49-point repositioning is a legitimate thing to
        # ask for and not a thing to do quietly.
        "same_class_swap_ils": round(swap_ils, 2),
        "swap_pct_of_nav": round(swap_ils / nav, 4) if nav else 0.0,
        "buying_class": buying_class,
    }


def funding_drift(rows, snap, fund: dict, buys_ils: dict, objective: str | None,
                  book_target: dict | None) -> dict:
    """Total drift from the book target before and after a funded multi-leg buy.

    PR 106. `propose_funded_buy` simulates its trade before it may claim
    anything; the sleeve funder did not, and on a Grow + 60/40 core book it sold
    Fixed Income (20% held, 32% target) to buy SOXL and called it "the most
    overweight class" -- total drift 0.44 -> 0.64. This is the same simulation
    for a buy spread across classes. `buys_ils` maps asset class -> shekels
    bought.

    PR 106.1: measured on the WHOLE book, cash included -- the book target has a
    Cash slice, and `/mix` and `rank_trim_candidates` both weigh against NAV.
    106 measured invested holdings only against a target that included cash, so
    on the live book it put Fixed Income at 12.5% instead of 12.11% (target
    12.14%) and its refusal could not name the class it was protecting.

    Returns ``before``, ``after``, ``rises`` and ``sold_at_or_under_target``:
    the classes a sell leg takes from that were not over their target.
    """
    targets = targets_for(objective, book_target)
    before: dict[str, float] = {}
    for p in _position_rows(rows, snap):
        before[p["asset_class"]] = before.get(p["asset_class"], 0.0) + p["value_ils"]
    nav = float((snap or {}).get("nav") or 0.0)
    cash = max(0.0, nav - sum(before.values()))
    if cash > 0:
        before["Cash"] = before.get("Cash", 0.0) + cash
    after = dict(before)
    for cls, amt in (buys_ils or {}).items():
        after[cls] = after.get(cls, 0.0) + float(amt or 0.0)
    if (fund or {}).get("from_cash_ils"):
        after["Cash"] = after.get("Cash", 0.0) - float(fund["from_cash_ils"])
    sold_classes = set()
    for s in (fund or {}).get("sells", []) or []:
        cls = s.get("asset_class")
        if cls:
            after[cls] = after.get(cls, 0.0) - float(s.get("value_ils") or 0.0)
            sold_classes.add(cls)

    def drift(values: dict) -> float:
        tot = sum(values.values())
        if tot <= 0:
            return 0.0
        return sum(abs(values.get(c, 0.0) / tot - float(targets.get(c, 0.0) or 0.0))
                   for c in set(values) | set(targets))

    tot_b = sum(before.values()) or 1.0
    under = sorted(c for c in sold_classes
                   if before.get(c, 0.0) / tot_b <= float(targets.get(c, 0.0) or 0.0) + 1e-9)
    d0, d1 = drift(before), drift(after)
    return {"before": round(d0, 4), "after": round(d1, 4), "rises": d1 > d0 + 1e-4,
            "sold_at_or_under_target": under,
            "targets": {c: round(float(targets.get(c, 0.0) or 0.0), 4) for c in under},
            "held": {c: round(before.get(c, 0.0) / tot_b, 4) for c in under}}


def drift_refusal(gate: dict) -> str:
    """The sentence that says WHICH class the plan is short of."""
    def pct(x: float) -> str:      # 20% / 12.11% -- as many digits as tell them apart
        return f"{x * 100:.2f}".rstrip("0").rstrip(".") + "%"
    parts = [f"{c} ({pct(gate['held'][c])} held, {pct(gate['targets'][c])} target)"
             for c in gate.get("sold_at_or_under_target") or []]
    named = (" It would have to sell " + ", ".join(parts)
             + ", which your plan says you hold too little of.") if parts else ""
    # In points: "0.05 -> 0.05" hid a real rise (0.0496 -> 0.0504, 106.1's live book).
    return (f"Funding this would move the book further from your plan "
            f"(off target by {gate['before'] * 100:.1f} -> {gate['after'] * 100:.1f} points in total).{named}")


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
        return "You don't have spendable cash above your cash target, and nothing is overweight enough to trim."
    line = "Fund it with " + " and ".join(bits) + "."
    if fund.get("tax_ils"):
        line += f" Estimated tax on the sale: ₪{fund['tax_ils']:,.0f}."

    # SPENDING THE RESERVE IS SAID OUT LOUD, WITH THE REASON THAT BOUGHT IT.
    #
    # Eran, 2026-09-06: "if you recommend me to use my cash for this investment
    # you should explain why I should go below the 7.5%". Three facts and his
    # own why, in that order -- how much of the reserve, where cash lands, and
    # that the hard floor is untouched. The reason is the CALLER'S: `plan_funding`
    # will not set `reserve_used_ils` without one, so this sentence cannot be
    # printed with nothing behind it.
    _res = float(fund.get("reserve_used_ils") or 0.0)
    if _res >= 1.0:
        _why = fund.get("reserve_reason") or ""
        _pct = float(fund.get("cash_after_pct") or 0.0)
        _floor = float(fund.get("cash_hard_floor_ils") or 0.0)
        line += (f" This spends ₪{_res:,.0f} of your cash reserve, taking cash to "
                 f"{_pct:.1%} against your {float(fund.get('cash_target_pct', fund.get('cash_floor_pct')) or 0.0):.1%} "
                 f"target (₪{float(fund.get('cash_after_ils') or 0.0):,.0f} left, "
                 f"hard floor ₪{_floor:,.0f} untouched).")
        if _why:
            line += f" Worth it because {_why}" + ("" if _why.endswith(".") else ".")

    # The honesty clause. Selling equities to buy equities is a legitimate
    # trade -- swapping international and dividend exposure for factor exposure
    # is a real decision -- but it is NOT the rebalance the per-sale reasons
    # sound like, and the user has to be told which one they are agreeing to.
    sold = {s.get("asset_class") for s in fund.get("sells", []) if s.get("asset_class")}
    if buying_class and sold and sold == {buying_class}:
        line += (f" This does not change your {buying_class} weight — the proceeds buy "
                 f"{buying_class} again. It swaps which {buying_class.lower()} you hold.")

    # A SHORTFALL THAT ROUNDS TO ZERO IS NOT A SHORTFALL, and saying so out loud
    # is a false statement on a card that is otherwise fully funded:
    #
    #     "Fund it with 70,078 by selling 188.8887 HUGE. ... That still leaves
    #      0 short."
    #
    # `shortfall_ils` is stored to the agora, so any residue under half a shekel
    # formats as "0" while still being truthy. The sentence then contradicts the
    # rest of the line and, worse, contradicts the card's own `est_amount`.
    #
    # Caught by `test_no_card_is_presented_short[single_name_breach]`, which has
    # asserted this all along -- it went unhit because no existing producer had
    # yet hit the round-to-zero case. The buy-idea cards did on their first run.
    # Threshold, not truthiness: below the minimum tradeable amount there is
    # nothing a reader could act on, so there is nothing to report.
    _short = float(fund.get("shortfall_ils") or 0.0)
    if _short >= min_trade_ils():
        line += f" That still leaves ₪{_short:,.0f} short."
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
                       ledger: "FundingLedger | None" = None,
                       book_target: dict | None = None,
                       allow_reserve: bool = False,
                       reserve_reason: str | None = None,
                       name_caps: dict | None = None,
                       name_targets: dict | None = None,
                       name_floors: dict | None = None,
                       owner=None) -> FundedBuy | None:
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

    ``owner`` names the card this purchase becomes -- a card id, or a callable
    taking the `FundedBuy` and returning one when the id carries the amount. The
    ledger skips the commit of a card the user has already muted (PR 164).
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

    targets = targets_for(objective, book_target)
    class_target = float(targets.get(buying_class, 0.0) or 0.0)

    # What the PLAN wants here, and separately the most this NAME may take.
    #
    # THE MOST THIS NAME MAY TAKE IS THE TIGHTER OF TWO CAPS. This read the
    # plan-wide `concentration_cap` alone, so a hand-set `max_weight` rule -- a
    # limit the user set on that specific position -- did not bound the purchase
    # at all. Measured 2026-09-06 on a book with NVDA at 10% and a 12% cap, with
    # Equities under its Grow target so the buy was otherwise legitimate: this
    # sized 4,500 and took NVDA to 40.0%, stopping only at the plan-wide cap,
    # 28 points past the user's own limit -- and the rule then fired a card
    # telling him the cap had been breached. A cap that alerts after the trade
    # it could have prevented is not a cap.
    gap = class_gap_ils(nav, mix, buying_class, class_target)
    room = name_room_ils(nav, ticker_weight, effective_name_cap(cap, tk, name_caps))
    want = min(gap, room) if gap >= min_trade_ils() else room
    if gap < min_trade_ils() and not allow_within_class_swap:
        # The class is at or over target. Absent an explicit opt-in there is no
        # honest card here: buying more of a class the plan already holds too
        # much of cannot be a move toward the plan.
        return None
    if requested_ils is not None:
        want = min(want, max(0.0, float(requested_ils)))
    if want < min_trade_ils():
        return None

    ex = {t.upper() for t in (exclude or set())} | {tk}
    _res = dict(ledger.shares) if ledger is not None else None
    _used = ledger.cash_ils if ledger is not None else 0.0
    fund = plan_funding(rows, snap, plan, objective, cap, want, cash_ils=cash_ils,
                        exclude=ex, reserved=_res, cash_used=_used,
                        book_target=book_target, allow_reserve=allow_reserve,
                        reserve_reason=reserve_reason, name_caps=name_caps,
                        name_targets=name_targets, name_floors=name_floors)
    amount = min(want, float(fund.get("funded_ils") or 0.0))
    if amount < min_trade_ils():
        return None
    if amount < want:
        # Re-plan at the size the book can actually pay for, so the funding legs
        # describe the trade being proposed rather than one that was rejected.
        fund = plan_funding(rows, snap, plan, objective, cap, amount,
                            cash_ils=cash_ils, exclude=ex, reserved=_res, cash_used=_used,
                            book_target=book_target, allow_reserve=allow_reserve,
                            reserve_reason=reserve_reason, name_caps=name_caps,
                            name_targets=name_targets, name_floors=name_floors)
        amount = min(amount, float(fund.get("funded_ils") or 0.0))
        if amount < min_trade_ils():
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
    # PR 87: a class already at its target (gap under the minimum) admits ONLY
    # a swap. The sizing above lets such a buy take the name's whole room so a
    # same-class swap can be any size -- but when the funding came from cash or
    # another class it is not a swap, it is an overshoot. Production 2026-09-30:
    # Fixed Income 14.26% vs 14.60% (gap ~₪80) sized to ₪841 from cash + USO,
    # taking it to 17.5% and labelled "toward target".
    if gap < min_trade_ils() and kind != "within_class_swap":
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

    result = FundedBuy(
        ticker=tk, buying_class=buying_class, amount_ils=round(amount, 2), fund=fund,
        class_before=round(class_before, 4), class_after=round(class_after, 4),
        class_target=round(class_target, 4), kind=kind,
        summary=describe_funding(fund, buying_class), impact=impact,
        apply_spec={"kind": "buy_funded", "ticker": tk, "market": market,
                    "asset_class": buying_class, "amount_ils": round(amount, 2),
                    "from_cash_ils": fund.get("from_cash_ils", 0.0),
                    "sells": fund.get("sells", [])})
    if ledger is not None:
        # Only once the card is definitely being returned.
        _owner = owner(result) if callable(owner) else owner
        ledger.commit(fund, owner=_owner)
    return result


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
    # PR 164: cards the user has already muted (Ignore / Done). Such a card is
    # still BUILT -- the mute rules decide later whether it stays hidden -- but
    # its sales must not be spoken for. Live 2026-11-11: the ignored
    # "Diversify across regions" card claimed XLE and XLF, the only two holdings
    # outside the plan, and the visible VXUS band card was left selling VTI.
    skip: set = field(default_factory=set)

    def commit(self, fund: dict, owner: str | None = None) -> None:
        if owner is not None and owner in self.skip:
            return
        self.cash_ils += float((fund or {}).get("from_cash_ils") or 0.0)
        for s in (fund or {}).get("sells", []):
            tk = str(s.get("ticker") or "").upper()
            self.shares[tk] = self.shares.get(tk, 0.0) + float(s.get("shares") or 0.0)
