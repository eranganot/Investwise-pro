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
from app.services.plan_service import get_plan

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


def carved_classes(core_mix: dict | None, objective: str | None,
                   commodities_pct: float | None = None) -> set[str]:
    """The classes carved OUT of the core region (PR 71): {"Commodities"} when
    the core names none and the plan wants some. A holding in a carved class is
    not the core's -- it is judged against its own target, so the core card must
    not offer to sell it for being "not in the basket"."""
    if not core_mix or float(core_mix.get("Commodities") or 0.0):
        return set()
    want = (float(commodities_pct) if commodities_pct is not None else
            float((OBJ_TARGET.get(objective or "Balanced") or {}).get("Commodities") or 0.0))
    return {"Commodities"} if want > 0 else set()


def cap_held(held: dict[str, float], cap: float) -> dict[str, float]:
    """Scale the held-back weights so they sum to at most `cap` (a fraction of
    NAV): the cash a hold can actually reserve. Pure; see `book_target`."""
    tot = sum(max(0.0, float(v)) for v in (held or {}).values())
    if tot <= cap + 1e-12 or tot <= 0:
        return dict(held or {})
    f = max(0.0, cap) / tot
    return {k: float(v) * f for k, v in held.items() if float(v) * f > 1e-9}


def compose(*, sleeve_targets: dict[str, float], core_mix: dict[str, float] | None,
            objective: str, cash_pct: float = 0.0,
            commodities_pct: float | None = None,
            held_back: dict[str, float] | None = None) -> dict:
    """The pure half, so the arithmetic can be tested without a database.

    `cash_pct` is a deliberate reserve as a FRACTION of NAV -- dry powder, so
    there is something to buy an opportunity with. It comes out of the CORE's
    remainder and never out of the sleeves. That is the design decision, not an
    implementation detail: the sleeves are targets the user set one at a time,
    and scaling them to make room would silently manage a 38.4%-of-NAV sleeve
    to 35.5% -- the defect the docstring above is about.

    Default 0.0, which reproduces the previous target exactly, so a book with
    no reserve configured is managed exactly as it was.

    COMMODITIES ARE CARVED OUT OF THE CORE (PR 71, Eran 2026-09-22: "Yes, 10%
    carved from core"). A chosen core like Classic 60/40 names no Commodities,
    so this target used to hold none -- while `funding_service.targets_for`
    filled the gap from the objective table (Grow: 10%) and the buy cards
    bought toward it. The result summed to 110%: "Consider USO" bought
    Commodities toward 10% and the rebalance card, reading this target, sold
    them toward 0%. Now the commodity share is part of THE target:
    `commodities_pct` (default: the objective table's Commodities weight) comes
    out of the core's remainder, after the cash reserve, and only when the core
    has no opinion on Commodities itself (All-Weather does; 60/40 does not).
    Never out of the sleeves, for the same reason as cash.
    """
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

    # PR 75 -- A TAKE-PROFIT HOLD. `sleeve_targets` arrives with each held
    # ticker already cut to its current weight (`buyback_hold.apply_holds`);
    # the cut is parked in CASH here, before the core divides the remainder.
    # Without this the core would silently absorb the sleeve's shortfall and
    # the cash from the sale would buy other names of the same class.
    held = min(sum(max(0.0, float(v)) for v in (held_back or {}).values()), core_share)
    core_share -= held

    # The reserve is taken HERE, before the core divides what is left, and it is
    # CLAMPED to what the core actually has. Asking for 40% cash on a book whose
    # sleeves already claim 73% cannot be honoured without shrinking them, so it
    # is not honoured -- and `cash_clamped` says so, because a silent clamp is a
    # lie the size of the difference.
    want_cash = max(0.0, float(cash_pct or 0.0))
    cash = min(want_cash, core_share)
    clamped = want_cash > cash + 1e-9
    core_share -= cash

    comm = 0.0
    if core_mix and not float(core_mix.get("Commodities") or 0.0):
        want_comm = (float(commodities_pct) if commodities_pct is not None else
                     float((OBJ_TARGET.get(objective) or {}).get("Commodities") or 0.0))
        comm = min(max(0.0, want_comm), core_share)
        core_share -= comm

    base = core_mix or OBJ_TARGET.get(objective) or OBJ_TARGET["Balanced"]
    base_sum = sum(base.values()) or 1.0
    core = {c: (w / base_sum) * core_share for c, w in base.items()}

    target: dict[str, float] = dict(sleeves)
    for c, w in core.items():
        target[c] = target.get(c, 0.0) + w
    if cash > 0:
        # ADDED to whatever the core already holds in cash rather than
        # replacing it: the reserve is a floor the user asked for, not a
        # ceiling on what their core strategy may choose to hold.
        target["Cash"] = target.get("Cash", 0.0) + cash
    if comm > 0:
        target["Commodities"] = target.get("Commodities", 0.0) + comm
    if held > 0:
        target["Cash"] = target.get("Cash", 0.0) + held

    total = sum(target.values())
    if abs(total - 1.0) > _SUM_TOLERANCE:                        # pragma: no cover
        raise ValueError(f"book target sums to {total:.4f}, not 1.0: {target}")

    return {
        "target": _round_mix(target),
        "sleeves": _round_mix(sleeves),
        "core": _round_mix(core),
        "sleeved_pct": round(sleeved, 4),
        "core_pct": round(core_share, 4),
        "cash_pct": round(cash, 4),
        "cash_clamped": clamped,
        # The commodity carve-out, separate from the core so a card can say
        # where the Commodities target came from.
        "commodities_pct": round(comm, 4),
        # Weight parked in Cash by take-profit holds, per ticker (PR 75).
        "held_back": {k: round(float(v), 4) for k, v in (held_back or {}).items() if v > 0},
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

    # Stored as a PERCENTAGE on `plans`, like every other `_pct` column there
    # (`target_roi_pct` is 30.0 for 30%). Converted to a fraction exactly once,
    # here, so no other reader has to know which unit it is in.
    cash_pct = 0.0
    plan = None
    try:
        plan = await get_plan(session, user)
        from app.services.funding_service import cash_target_ils_of
        if cash_target_ils_of(plan) is not None:
            # A shekel target, as a share of today's book -- the same conversion
            # `funding_service.cash_floor_pct` makes, so /mix and the cards agree.
            from app.services.fx import book_nav_ils
            from app.services.intake_service import list_positions
            _nav = book_nav_ils(await list_positions(session, user))
            if _nav > 0:
                cash_pct = max(0.0, min(0.5, cash_target_ils_of(plan) / _nav))
        else:
            raw = getattr(plan, "cash_target_pct", None) if plan is not None else None
            if raw is not None:
                cash_pct = max(0.0, min(100.0, float(raw))) / 100.0
    except Exception:  # noqa: BLE001 - a book target must not fail on a plan read
        cash_pct = 0.0

    # PR 75: the planned sleeve targets stay as they are (`planned_sleeve_targets`
    # below); what the BUYERS are managed to honours any take-profit hold.
    planned = dict(targets)
    held: dict[str, float] = {}
    try:
        from app.services import buyback_hold as bh
        _holds = await bh.load(session, user)
        if _holds:
            from app.services.fx import book_nav_ils
            from app.services.intake_service import list_positions
            _rows = await list_positions(session, user)
            _act = bh.active(_holds, _rows)
            if _act:
                _nav_h = book_nav_ils(_rows)
                targets, held = bh.apply_holds(targets, _act, bh.weights_of(_rows, _nav_h))
                # PR 86: a hold reserves cash that EXISTS. The held-cash card
                # already caps its reserve at the cash above the floor
                # (`spendable_cash`); the target did not, so after Eran invested
                # part of the reserve elsewhere (2026-09-30: cash 1,863 -> 703,
                # ₪553 kept) the target still parked all 8.5% (₪1,996) in Cash
                # and the rebalancer asked him to sell ₪758 of Equities -- the
                # VTI he had just bought -- to rebuild money he chose not to keep.
                # The held ticker's target stays at its weight (still no buy);
                # the part not backed by cash flows to the core as normal.
                if held and _nav_h > 0:
                    from app.services.funding_service import spendable_cash
                    from app.services.intake_service import get_cash
                    _sp = spendable_cash(await get_cash(session, user), _nav_h,
                                         getattr(plan, "objective", None) or objective, plan)
                    held = cap_held(held, max(0.0, float(_sp)) / _nav_h)
    except Exception:  # noqa: BLE001 - a hold read must not cost the book target
        targets, held = planned, {}

    out = compose(sleeve_targets=targets, core_mix=core_mix, objective=objective,
                  cash_pct=cash_pct, held_back=held)
    out["planned_sleeve_targets"] = {k.upper(): round(float(v), 4) for k, v in planned.items()}
    if held:
        # What the book SHOWS as its sleeves stays the plan (review: the display
        # fields quoted the held-back weight as if the sleeve had shrunk). The
        # managed `target` above is the one with the hold parked in Cash.
        _ps = sleeve_class_mix(planned)
        out["sleeves"] = _round_mix(_ps)
        out["sleeved_pct"] = round(sum(_ps.values()), 4)
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


async def mix_report(session, user) -> dict:
    """GET /mix: the book against its target (PR 118a: moved from routes/plan.py)."""
    from app.engines.allocation_engine import AllocationEngine
    from app.services.allocation_mix import current_mix
    from app.services.plan_service import get_plan
    from app.services.plan_view import positions
    rows = await positions(session, user)
    current, nav = current_mix(rows)
    if not nav:
        return {"message": "Add holdings to check your mix."}
    plan = await get_plan(session, user)
    # The BOOK's target -- sleeves plus core -- from the one helper Today uses.
    # Sharing a helper was already the rule here; what it shared was a target
    # for 36% of the book, measured by both against all of it. Agreeing on a
    # wrong number is not the property this comment was claiming.
    bt = await book_target(session, user, plan.objective if plan else "Balanced")
    report = AllocationEngine().compute(target_allocation=bt["target"],
                                        current_allocation=current, nav=nav)
    out = report.model_dump()
    # Always surface Cash, even at 0%: a missing slice read as "fully invested"
    # when the truth was "cash isn't being tracked".
    if isinstance(out.get("current_allocation"), dict):
        out["current_allocation"].setdefault("Cash", 0.0)
    return {"note": "Holdings are classified roughly by ticker/venue.",
            "objective": plan.objective if plan else "Balanced",
            # The provenance, so a reader can see a 77% Equities target is 64
            # points of sleeve chosen one at a time plus 13 of core.
            "target_from_sleeves": bt["sleeves"], "target_from_core": bt["core"],
            "core_pct_of_book": bt["core_pct"], "target_source": bt["source"],
            "cash_ils": round(current.get("Cash", 0.0) * nav, 2), **out}
