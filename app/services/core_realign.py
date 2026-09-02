"""PR 39 -- the core card stops giving advice and does the thing.

Eran, 2026-09-02, looking at the three cards PR 38 shipped: *"I'm missing the
most important thing in these cards --> guidance is not enough... I want these
cards to act on my behalf (as other cards can do it)."*

PR 38 deliberately shipped them as guidance, on the grounds that this app cannot
fund a 3,392 ILS purchase from a 619 ILS cash balance. That reasoning was
half right and the conclusion was wrong. **The money is already in the book** --
it is sitting in the wrong places:

    V         954 ILS   in the core region, and not in the core's basket
    BND       594 ILS   above the core's own 40% leg
    sleeves ~1,270 ILS  above the sleeves' OWN target (79% held vs 73% set)

Bringing the core back to Classic 60/40 is not a purchase that needs new money.
It is a REBALANCE, and every leg of it is something the book already holds.

WHY THIS IS ONE FUNCTION AND NOT TWO
====================================
`_rebalance_to` records the lesson this module is built on: the card read one
target and the button applied another, so the screen said "sell 4,324" and
Accept sold 12,139. Fixing only the card would have been worse than fixing
neither -- at least a card and a button that are wrong together agree.

So the plan is computed HERE, once, and both the card and the Apply call it.
The card renders the legs this returns; the executor executes the legs this
returns. There is no second sizing anywhere.

PURE ON PURPOSE -- dicts in, a plan out, no ORM and no I/O, matching `blend`,
`split_solver`, `core_search` and `sleeve_count`. Every rule below is testable
without a database or a provider, which is what lets the tests assert the
arithmetic of a real trade rather than mocking one.

WHAT IT WILL NOT DO
===================
  * It will not take cash below the app's own floor.
  * It will not trim a sleeve BELOW its own target. The sleeves' sizes are a
    decision the user made on the Plan tab; this may reclaim what is over that
    decision and may not overrule it.
  * It will not buy more than the sells actually raised. If the proceeds fall
    short, it buys what they cover and NAMES THE REMAINDER, because a plan that
    silently half-executes is the thing the card-claim invariant exists to stop.
  * It will not touch a holding a sleeve claims, except through the explicit
    sleeve-trim leg, which is sized from the sleeve's own overshoot.

Not financial advice.
"""
from __future__ import annotations

#: Below this a leg is rounding, not a trade. Same floor the mute logic and the
#: PR 38 cards use, so one answer to "is this worth doing".
MIN_LEG_ILS = 100.0

#: Reasons a plan can come back with nothing to do, or with less than the whole
#: job. Each is a different fact and none of them is a silent no-op.
NOTHING_TO_DO = "ALREADY_ON_BASKET"
NO_BASKET = "CORE_HAS_NO_BASKET"
NO_CORE = "NO_CORE_STRATEGY"
SHORT_OF_FUNDS = "PARTIALLY_FUNDED"


def _round2(x: float) -> float:
    return round(float(x), 2)


def plan_realign(*, basket: dict[str, float], core_pct: float, nav: float,
                 held: dict[str, float], cash_ils: float, cash_floor_ils: float,
                 sleeve_over_ils: float = 0.0, cgt_rate: float = 0.0,
                 gain_frac: dict[str, float] | None = None,
                 allow_sleeve_trim: bool = True) -> dict:
    """What it would take to bring the core back to the basket it names.

    ``held`` is the CORE REGION only -- what no sleeve claims and is not cash,
    in ILS. ``sleeve_over_ils`` is how much the sleeves hold ABOVE their own
    target; it is the only money this plan may take from them, and it is
    computed by the caller from `sleeve_occupancy` rather than guessed here.

    ``gain_frac`` maps a ticker to the fraction of its value that is unrealised
    GAIN, so tax on a partial sale is estimated from the same shape the executor
    will use. Absent, tax is treated as zero and `tax_ils` says so by being 0.0 --
    never by being omitted, because a missing number reads as "no tax" too.

    Returns a plan whose legs are in EXECUTION ORDER: sells, then the sleeve
    trim, then buys. That order is not cosmetic -- the buys are funded by the
    sells, and running them the other way round would buy money the book does
    not have yet.
    """
    gain_frac = gain_frac or {}
    if core_pct <= 0 or nav <= 0:
        return {"ok": False, "reason": NO_CORE, "legs": [],
                "detail": "there is no core to realign"}
    bsum = sum(w for w in basket.values() if w > 0)
    if bsum <= 0:
        return {"ok": False, "reason": NO_BASKET, "legs": [],
                "detail": "this core names no basket, so there is nothing to hold it to"}

    want = {t.upper(): (w / bsum) * core_pct * nav
            for t, w in basket.items() if w > 0}
    have = {t.upper(): float(v) for t, v in held.items() if float(v) > 0}

    sells: list[dict] = []
    buys: list[dict] = []

    # 1) EVERY HOLDING IN THE CORE REGION THE BASKET DOES NOT NAME.
    #    Sold in full: a half-exit leaves the core still holding something its
    #    strategy does not, which is the condition being fixed.
    for tk in sorted(set(have) - set(want)):
        amt = have[tk]
        if amt >= MIN_LEG_ILS:
            sells.append({"ticker": tk, "ils": _round2(amt), "kind": "not_in_basket",
                          "why": "not in the core's basket", "full_exit": True})

    # 2) EVERY LEG HELD ABOVE ITS SHARE. Trimmed to the leg, never past it.
    for tk in sorted(want):
        over = have.get(tk, 0.0) - want[tk]
        if over >= MIN_LEG_ILS:
            sells.append({"ticker": tk, "ils": _round2(over), "kind": "over_leg",
                          "why": f"above its {want[tk] / nav:.1%} leg", "full_exit": False})

    # 3) EVERY LEG HELD BELOW ITS SHARE. What the plan is FOR.
    for tk in sorted(want):
        under = want[tk] - have.get(tk, 0.0)
        if under >= MIN_LEG_ILS:
            buys.append({"ticker": tk, "ils": _round2(under),
                         "why": f"to reach its {want[tk] / nav:.1%} leg"})

    if not sells and not buys:
        return {"ok": False, "reason": NOTHING_TO_DO, "legs": [],
                "detail": "the core is already holding its basket"}

    # ---------------------------------------------------------------- #
    # FUNDING. Sells first, net of tax, then cash above the floor, then --
    # only if still short -- what the sleeves hold above their OWN target.
    # ---------------------------------------------------------------- #
    tax = 0.0
    for s in sells:
        tax += max(0.0, float(gain_frac.get(s["ticker"], 0.0))) * s["ils"] * cgt_rate
    raised = sum(s["ils"] for s in sells) - tax

    spendable = max(0.0, float(cash_ils) - float(cash_floor_ils))
    need = sum(b["ils"] for b in buys)

    sleeve_leg = None
    short = need - (raised + spendable)
    if short >= MIN_LEG_ILS and allow_sleeve_trim and sleeve_over_ils >= MIN_LEG_ILS:
        # ONLY THE OVERSHOOT. The sleeve sizes are a decision made on the Plan
        # tab; this may reclaim what is above that decision and may never
        # overrule it, however short the buy still is.
        take = min(short, float(sleeve_over_ils))
        if take >= MIN_LEG_ILS:
            s_tax = max(0.0, float(gain_frac.get("__sleeves__", 0.0))) * take * cgt_rate
            sleeve_leg = {"ils": _round2(take), "tax_ils": _round2(s_tax),
                          "why": "held above the sleeve sizes you set"}
            tax += s_tax
            raised += take - s_tax
            short = need - (raised + spendable)

    funded = min(need, max(0.0, raised + spendable))
    # Scale the buys to what is actually funded, largest gap first, so a partial
    # plan closes the biggest hole rather than spreading a shortfall evenly and
    # leaving every leg wrong.
    left = funded
    for b in sorted(buys, key=lambda x: -x["ils"]):
        take = min(b["ils"], left)
        b["funded_ils"] = _round2(take if take >= MIN_LEG_ILS else 0.0)
        left = max(0.0, left - b["funded_ils"])
    shortfall = _round2(max(0.0, need - sum(b["funded_ils"] for b in buys)))

    legs: list[dict] = []
    for s in sells:
        legs.append({**s, "action": "sell"})
    if sleeve_leg:
        legs.append({**sleeve_leg, "action": "trim_sleeves", "ticker": None})
    for b in sorted(buys, key=lambda x: -x["ils"]):
        if b["funded_ils"] > 0:
            legs.append({"action": "buy", "ticker": b["ticker"],
                         "ils": b["funded_ils"], "wanted_ils": b["ils"],
                         "why": b["why"]})

    return {
        "ok": bool(legs),
        "reason": (SHORT_OF_FUNDS if shortfall >= MIN_LEG_ILS else None),
        "legs": legs,
        "sells_ils": _round2(sum(s["ils"] for s in sells)),
        "sleeve_trim_ils": _round2(sleeve_leg["ils"] if sleeve_leg else 0.0),
        "cash_used_ils": _round2(min(spendable, max(0.0, need - raised))),
        "buys_ils": _round2(sum(b["funded_ils"] for b in buys)),
        "tax_ils": _round2(tax),
        "shortfall_ils": shortfall,
        "want_ils": {t: _round2(v) for t, v in sorted(want.items())},
        "have_ils": {t: _round2(v) for t, v in sorted(have.items())},
        "detail": (f"{shortfall:,.0f} ILS of the core is still unfunded after "
                   f"selling what the basket does not name, trimming what is over "
                   f"its leg, and taking the sleeves back to the sizes you set"
                   if shortfall >= MIN_LEG_ILS else
                   "every leg is funded from money the book already holds"),
    }


def describe(plan: dict) -> list[str]:
    """The plan as the lines a card must show BEFORE the button is pressed.

    The card-claim invariant in one function: whatever this returns is what
    Apply will do, because both read the same plan. A leg that executes and is
    not in this list is a bug, and so is a line here with no leg behind it.
    """
    out: list[str] = []
    for leg in plan.get("legs") or []:
        amt = f"₪{round(leg.get('ils') or 0):,}"
        if leg["action"] == "sell":
            verb = "Sell all" if leg.get("full_exit") else "Trim"
            out.append(f"{verb} {leg['ticker']} — about {amt} ({leg['why']})")
        elif leg["action"] == "trim_sleeves":
            out.append(f"Trim your sleeves back to the sizes you set — about {amt}")
        else:
            want = leg.get("wanted_ils")
            tail = (f" of the {f'₪{round(want):,}'} it needs"
                    if want and round(want) != round(leg["ils"]) else "")
            out.append(f"Buy {leg['ticker']} — about {amt}{tail}")
    if (plan.get("tax_ils") or 0) > 0:
        out.append(f"Estimated tax on the sales: about ₪{round(plan['tax_ils']):,}")
    if (plan.get("shortfall_ils") or 0) >= MIN_LEG_ILS:
        out.append(f"About ₪{round(plan['shortfall_ils']):,} would still be unfunded — "
                   f"that needs new money, and this will say so again next time")
    return out
