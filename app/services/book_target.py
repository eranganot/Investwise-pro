"""What mix the WHOLE book is managed to, when 64% of it is deliberately sleeved.

THE DEFECT THIS EXISTS TO FIX, measured on the live book on 2026-08-25.

`recommendations.py` built the rebalance card like this:

    mix, _ = current_mix(rows)                            # the WHOLE book
    target = await _core_target(session, user, objective) # the CORE's mix
    report = AllocationEngine().compute(target, mix, nav=nav)   # sized on FULL nav

The core managed **36%** of the book. Its target was measured against 100% of
it and sized off the full NAV, so every leg came out overstated by exactly
1 / 0.36 = 2.78x:

    the card said            correct for a 36% core
    Sell Equities  12,139    4,324
    Buy Fixed Inc   8,469    3,054
    Buy Commodities 5,293    1,908

Worse, the top card on Today read "TQQQ is 39% of your portfolio - trim" on a
book whose `btm_swing_dip` sleeve TARGETS 38.4% of NAV in TQQQ and was sitting
at 38.59%. The app was telling him to destroy a sleeve it had set itself.

And this got worse the day a core was chosen. Before that, `_core_target` fell
back to `OBJ_TARGET[objective]` -- Grow, 80/10/10 -- which at least looks like a
whole-book target, so the error was small enough to read as ordinary drift.
Choosing All-Weather (35/40/25) turned a 3,600 equity sale into a 12,139 one.
The recommendation was fine; the arithmetic behind the card was not.

THE FIX. A target that governs part of the book is not a target for the book.
The book's target is the two of them ADDED:

    sleeves:  every sleeve's per-ticker target, classified and summed
              -> {"Equities": 0.64}
    core:     the core strategy's mix, scaled to the share the sleeves left
              -> 0.36 x {35/40/25} = {"Equities": .126, "Fixed Income": .144,
                                      "Commodities": .09}
    book:     {"Equities": 0.766, "Fixed Income": 0.144, "Commodities": 0.09}

which sums to 1.0 by construction, because the core's share is computed as the
remainder rather than read from a second place that could disagree.

WHY IT MUST SUM TO 1.0. `AllocationEngine.compute` never checks. A class in
`current_allocation` but absent from `target_allocation` gets `target = 0.0` and
a SELL sized at the entire holding; a target summing to less than 1 therefore
emits sells that fund nothing. `_rebalance_to` renormalises downstream
(`present_weight`), which hides the problem at the point where it would
otherwise be caught. So this module asserts the sum instead of papering it.
"""
from __future__ import annotations

from app.services.allocation_mix import OBJ_TARGET, classify

# A book target is a set of weights over asset classes. Rounding each class to
# 4dp can leave the sum a hair off 1.0; anything bigger than this is a bug in
# the inputs, not float noise.
_SUM_TOLERANCE = 0.005


def _round_mix(mix: dict[str, float]) -> dict[str, float]:
    return {c: round(w, 4) for c, w in mix.items() if round(w, 4) > 0}


def sleeve_class_mix(sleeve_targets: dict[str, float]) -> dict[str, float]:
    """{ticker: share of NAV} -> {asset class: share of NAV}.

    `all_sleeve_targets` is per-TICKER, and nothing in the app turned that into
    per-class -- which is why the rebalance card could not see that the sleeves
    already account for 64 points of Equities and went on demanding they be
    sold. Market is unknown here and does not need to be: `classify` resolves
    TQQQ/SOXL by name, and the commodity and bond ETF sets by membership.
    """
    out: dict[str, float] = {}
    for tk, w in (sleeve_targets or {}).items():
        cls = classify(tk, "")
        out[cls] = out.get(cls, 0.0) + float(w)
    return out


def compose(*, sleeve_targets: dict[str, float], core_mix: dict[str, float] | None,
            objective: str) -> dict:
    """The pure half, so the arithmetic can be tested without a database."""
    sleeves = sleeve_class_mix(sleeve_targets)
    sleeved = sum(sleeves.values())

    # Defensive, and it has bitten before: sleeve percentages are validated to
    # sum to <= 100 at write time, but a catalogue edit that changed a basket
    # could push the computed total past 1.0. Scale rather than emit a target
    # with a negative core.
    if sleeved > 1.0:
        sleeves = {c: w / sleeved for c, w in sleeves.items()}
        sleeved = 1.0

    # The core's share is the REMAINDER, deliberately. `sleeve_service` computes
    # core_pct the same way; deriving it here rather than passing it in means
    # the two cannot drift, and the total cannot fail to be 1.0.
    core_share = max(0.0, 1.0 - sleeved)
    base = core_mix or OBJ_TARGET.get(objective) or OBJ_TARGET["Balanced"]
    base_sum = sum(base.values()) or 1.0
    core = {c: (w / base_sum) * core_share for c, w in base.items()}

    target: dict[str, float] = dict(sleeves)
    for c, w in core.items():
        target[c] = target.get(c, 0.0) + w

    total = sum(target.values())
    if abs(total - 1.0) > _SUM_TOLERANCE:                        # pragma: no cover
        raise ValueError(f"book target sums to {total:.4f}, not 1.0: {target}")

    return {
        "target": _round_mix(target),
        "sleeves": _round_mix(sleeves),
        "core": _round_mix(core),
        "sleeved_pct": round(sleeved, 4),
        "core_pct": round(core_share, 4),
    }


async def book_target(session, user, objective: str) -> dict:
    """THE target. One function, so two screens cannot quote two answers.

    Returns the composed mix plus its provenance, because a card that says
    "your Equities target is 77%" has to be able to say WHERE 77 came from --
    64 points of it are sleeves the user chose one at a time, and 13 are the
    core. Reporting only the total is how the previous version was wrong for
    twelve days without anyone being able to see it on the screen.
    """
    from app.services import sleeve_service as sv
    from app.services import strategies as static_cat

    targets = await sv.all_sleeve_targets(session, user)
    sid = await sv.core_strategy_id(session, user)
    entry = None
    for c in static_cat.CATALOG:
        if c["id"] == sid:
            entry = c
            break
    core_mix = (entry or {}).get("target_allocation") or None

    out = compose(sleeve_targets=targets, core_mix=core_mix, objective=objective)
    out["core_strategy_id"] = sid if core_mix else None
    out["core_strategy_name"] = (entry or {}).get("name") if core_mix else None
    # Named so a card can say which of the two it is quoting rather than
    # implying the book has one manager.
    out["source"] = ("sleeves+core" if targets and core_mix else
                     "sleeves+objective" if targets else
                     "core" if core_mix else "objective")
    out["objective"] = objective
    return out


def explain(bt: dict, cls: str) -> str:
    """One line naming where a class's target came from. For card copy.

    "77% (64% your sleeves + 13% your core)" is checkable on the screen.
    "77%" is not, and 77% is the number that was wrong.
    """
    t = bt["target"].get(cls, 0.0)
    s = bt["sleeves"].get(cls, 0.0)
    c = bt["core"].get(cls, 0.0)
    if s and c:
        return f"{t:.0%} ({s:.0%} your sleeves + {c:.0%} your core)"
    if s:
        return f"{t:.0%} (all of it your sleeves)"
    if c:
        core = bt.get("core_strategy_name")
        return f"{t:.0%} (your core{f', {core}' if core else ''}, which is {bt['core_pct']:.0%} of the book)"
    return f"{t:.0%}"
