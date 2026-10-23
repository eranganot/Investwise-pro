"""The book's sleeves: which strategies it runs, and how much of it each governs.

This is the read half of "core + N sleeves". Everything here is deliberately
inert with respect to the running app: nothing in this module arms a rule, funds
a leg, sizes a trade or writes to ``plans``. The one write it does perform is a
one-shot backfill of the single strategy the ``plans`` columns can already hold,
so the new table starts out agreeing with the old one instead of reading empty
for someone who has a strategy applied.

**The core is the implicit remainder, and since C6 it also has a name.** Sleeves
sum to <= 100 and the rest of the book stays objective-managed. C1 reserved
``is_core`` for the other model -- the core as an explicit row so the sleeves sum
to exactly 100 -- and deliberately never wrote it.

C6 writes it, for a narrower purpose than that model intended, and the
distinction is the whole design: **the core row records WHICH strategy manages
the core, never HOW BIG it is.** ``sleeve_pct`` on a core row is always 0 and the
core's size stays computed by ``remainder_pct``. So C1's decision stands -- the
core is still a remainder -- while "my core is managed by 60/40 Balanced" becomes
a thing the book can state instead of a thing the user has to infer from an
objective dropdown.

Everything that reads sleeves therefore filters the core row OUT. A core row that
leaked into ``total_pct`` would shrink the book by nothing at all, and one that
leaked into ``all_sleeve_targets`` would arm ``max_weight`` caps on the core's
whole basket. ``list_sleeves`` is the single choke point for that.

Two invariants live here rather than in a route, because the phases after this
one (add/update/remove a sleeve, the per-ticker cap sum, the funding exclusion
set) all need the same answers and must not each grow their own:

* the sleeves may not claim more of the book than there is;
* one strategy appears at most once, which the unique constraint also enforces
  at the database -- two rows for the same strategy at two sizes is the exact
  ambiguity the single-column design was being replaced to remove.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import PlanSleeve, User

logger = logging.getLogger(__name__)

# The whole book, in the unit the sleeve slider and its label already use.
BOOK_PCT = 100.0

# Below this, a "remainder" is rounding noise rather than a core worth naming.
_EPSILON_PCT = 0.05

# Marks the one-shot backfill as spent, for the deploy's whole lifetime.
#
# Deliberately a global one-shot rather than "top up any subject with no rows".
# The self-healing version reads better right up until C2 adds *remove a
# sleeve*: removing your only sleeve leaves you with no rows, and the next
# restart would hand it back. A migration that resurrects a decision the user
# made is worse than one that runs a beat too early.
BACKFILL_KEY = "plan_sleeves_backfilled_v1"

# The C6 sibling: a `plans.strategy` naming a STATIC family becomes the core row.
# Its own key, because C1's backfill has already run everywhere and reusing the
# key would mean it never fires. See `backfill_core_once`.
CORE_BACKFILL_KEY = "plan_core_backfilled_v1"


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
async def list_sleeves(session: AsyncSession, user: User) -> list[PlanSleeve]:
    """Every sleeve on this book, oldest first so the order is stable.

    **Excludes the core row.** This is the choke point that keeps C6 from
    changing anything about sleeves: the core is not a sleeve, does not claim a
    share of the book, and must not reach the cap arming or the funding
    arithmetic. Every existing caller keeps the answer it had before C6.
    """
    return list((await session.scalars(
        select(PlanSleeve)
        .where(PlanSleeve.subject == user.email, PlanSleeve.is_core.is_(False))
        .order_by(PlanSleeve.created_at, PlanSleeve.strategy_id))).all())


def total_pct(sleeves) -> float:
    return round(sum(float(s.sleeve_pct or 0.0) for s in sleeves), 4)


def remainder_pct(sleeves) -> float:
    """What the sleeves have not claimed -- the implicit, objective-managed core.

    Floored at zero rather than allowed to go negative: an over-allocated book is
    a thing to refuse at write time (see ``validate``), not a negative core to
    render on a card.
    """
    return round(max(0.0, BOOK_PCT - total_pct(sleeves)), 4)


def as_dicts(sleeves) -> list[dict]:
    # created_at is carried because "which boot wrote this?" turned out to be a
    # real question the first time C1 deployed: the backfill's log line is
    # emitted once, on one boot, and a later restart is silent by design. The
    # row's own timestamp answers it without log archaeology.
    return [{"strategy_id": s.strategy_id,
             "sleeve_pct": round(float(s.sleeve_pct or 0.0), 4),
             "is_core": bool(s.is_core),
             "created_at": (s.created_at.isoformat()
                            if getattr(s, "created_at", None) else None)}
            for s in sleeves]


# --------------------------------------------------------------------------- #
# Invariants -- pure, so C2..C4 can all ask the same question
# --------------------------------------------------------------------------- #
def validate(sleeves, *, strategy_id: str, sleeve_pct: float,
             replacing: bool = True) -> str | None:
    """Why this sleeve may not be added at this size, or None if it may.

    ``replacing`` is the update case: re-sizing a sleeve already on the book
    frees its current share first, so raising 20% to 25% is checked against the
    other sleeves rather than against a total that still counts the old 20%.

    Returns a reason a card can print, in the same voice ``_fund_sleeve`` uses
    when it abstains -- a refusal that only says "no" makes the user guess which
    number to change.
    """
    try:
        pct = float(sleeve_pct)
    except (TypeError, ValueError):
        return "a sleeve size must be a number"
    if pct <= 0:
        return "a sleeve has to be worth something — 0% is a removal, not a size"
    if pct > BOOK_PCT:
        return f"a sleeve cannot be more than the whole book ({BOOK_PCT:g}%)"

    sid = (strategy_id or "").strip()
    if not sid:
        return "a sleeve needs a strategy"

    others = [s for s in sleeves
              if not (replacing and s.strategy_id == sid)]
    if not replacing and any(s.strategy_id == sid for s in sleeves):
        return (f"{sid} is already a sleeve on this book — change its size "
                f"instead of adding it twice")

    free = BOOK_PCT - total_pct(others)
    if pct > free + _EPSILON_PCT:
        return (f"that would allocate {total_pct(others) + pct:g}% of the book. "
                f"There is {max(0.0, free):g}% left after the sleeves you already "
                f"run — lower this one, or shrink another first")
    return None


# --------------------------------------------------------------------------- #
# What the sleeves collectively want to hold
# --------------------------------------------------------------------------- #
async def live_sleeve_targets(session: AsyncSession, user: User) -> dict[str, dict]:
    """strategy_id -> the target its rule currently wants, from the stored signal.

    One read per request, passed down to the pure ``sleeve_targets`` rather than
    making that function async. Everything that asks "what does this sleeve want
    to hold" then gets the same answer, which is the whole point: before PR 63
    the Plan tab, the drift cards, the funding engine and the armed caps all read
    the catalogue's static weights, and so all described the aggressive
    instrument on every day of the year -- including the ~85% of days a swing
    rule is out of its setup.

    NO STALENESS GATE, and that is a decision rather than an omission. The
    obvious guard -- ignore a target older than N days, fall back to the
    catalogue -- fails in the dangerous direction: the catalogue default is
    always the LEVERAGED leg, so a dead evaluator would quietly turn "the rule
    says hold QQQ" back into "the sleeve wants TQQQ", and the app would start
    recommending its way into a 3x fund on the strength of no signal at all. The
    last thing the rule actually said is strictly more informative than a default
    that asserts a signal nobody produced. The age travels with the answer
    instead -- ``sleeve_occupancy`` reports ``signal_as_of`` -- so a stale
    evaluation is visible rather than silently overruled.

    ``strategy_signal_service.evaluate`` already refuses to STORE a target
    derived from closes more than ``MAX_FEED_AGE_DAYS`` old, so nothing here can
    be built on a stale price. What can go stale is the evaluation itself, and
    that is exactly what ``as_of`` says.
    """
    from app.models.tables import StrategySignalState

    rows = (await session.execute(
        select(StrategySignalState).where(StrategySignalState.subject == user.email)
    )).scalars().all()
    return {r.strategy_id: {"target": dict(r.target or {}), "as_of": r.as_of or ""}
            for r in rows}


def _live_for(live: dict | None, strategy_id: str) -> dict | None:
    """The stored target for one sleeve, or None. Keeps the call sites one line."""
    return ((live or {}).get(strategy_id) or {}).get("target") or None


async def all_sleeve_targets(session: AsyncSession, user: User) -> dict[str, float]:
    """Every sleeve's target weights, SUMMED PER TICKER across the whole book.

    This is the primitive the N-sleeve safety rules are built on, and summing is
    the whole point of it:

    * **One cap per ticker.** Two sleeves both wanting TQQQ must arm one
      ``max_weight`` at their combined size, not two competing ones. Two rules on
      one ticker at two levels is the P1 duplicate bug, back at N scale.
    * **One exclusion set.** The tax harvester and the funding engine must know
      about every sleeve, not just the one ``plans.strategy`` happens to name.
      Funding sleeve A by selling sleeve B is the failure this prevents.

    Weights are shares of NAV, so they add directly: a 10% SOXL sleeve and a
    15% sleeve that is 40% SOXL contribute 0.10 and 0.06 to the same ticker.
    """
    # Local import: strategy_service imports this module, and sleeve_targets is
    # a pure catalog lookup with no session of its own.
    from app.services.strategy_service import sleeve_targets

    live = await live_sleeve_targets(session, user)
    out: dict[str, float] = {}
    for s in await list_sleeves(session, user):
        for tk, w in (sleeve_targets(s.strategy_id, s.sleeve_pct,
                                     live=_live_for(live, s.strategy_id)) or {}).items():
            tk = tk.upper()
            out[tk] = out.get(tk, 0.0) + float(w)
    return out


async def sleeves_holding(session: AsyncSession, user: User, ticker: str) -> list[dict]:
    """Every sleeve whose target includes `ticker`, with how much of it.

    `ticker_pct` is that ticker's share of NAV inside the sleeve (the same
    number `all_sleeve_targets` sums), so shrinking the sleeve by
    `sold_pct / ticker_pct` of its size removes exactly `sold_pct` of the ticker.
    Largest holder first -- the one a "sell and lower the sleeve" choice resizes.
    """
    from app.services.strategy_service import sleeve_targets

    tk = (ticker or "").upper()
    live = await live_sleeve_targets(session, user)
    out = []
    for s in await list_sleeves(session, user):
        w = {k.upper(): float(v) for k, v in (sleeve_targets(
            s.strategy_id, s.sleeve_pct, live=_live_for(live, s.strategy_id)) or {}).items()}
        if w.get(tk):
            out.append({"strategy_id": s.strategy_id, "sleeve_pct": float(s.sleeve_pct or 0),
                        "ticker_pct": w[tk] * 100.0,
                        "n_tickers": sum(1 for v in w.values() if v)})
    out.sort(key=lambda d: -d["ticker_pct"])
    return out


async def sleeve_tickers(session: AsyncSession, user: User) -> set[str]:
    """Just the names. The exclusion set, for callers that do not need weights."""
    return set(await all_sleeve_targets(session, user))


async def sleeve_occupancy(session: AsyncSession, user: User) -> dict:
    """What each sleeve TARGETS against what the book actually HOLDS.

    No function returned this. `sleeve_pct` is a target and was rendered in the
    present tense -- the Sleeves card read "Your sleeves - 64% of the book"
    while the sleeves held 15% of it. Both numbers were correct answers to
    different questions the screen never distinguished, and the one it printed
    was wrong by 49 points.

    Deliberately NOT built on `_legs_for`: that drops any ticker already at its
    target, because it exists to compute what is left to BUY. Occupancy needs
    the opposite -- a ticker that is "done" is precisely a held one -- so
    reusing it would report a fully funded sleeve as holding nothing.

    Cash is excluded by `_held_ils`, so `held_pct` answers "how much of my book
    is this strategy running".

    Per sleeve: target_pct, held_ils, held_pct, gap_pct (signed; positive means
    under-filled). Plus book totals.
    """
    from app.services.intake_service import list_positions
    from app.services.strategy_service import _held_ils, _snapshot, sleeve_targets

    rows = await list_positions(session, user)
    snap = _snapshot(rows)
    nav = float(snap.get("nav") or 0.0)
    held = _held_ils(rows, snap)

    sleeves = await list_sleeves(session, user)
    live = await live_sleeve_targets(session, user)
    # ONE HOLDING, SPLIT BETWEEN THE SLEEVES THAT WANT IT. PR 131 (D95). Each
    # sleeve used to be credited with the WHOLE position of every ticker it
    # targets, so two sleeves on TQQQ both "held" all of it: on the owner's book
    # the rows read 45.7% / 38.4% / 13.5% (97.6%) while the sleeves together
    # held 59.2% against a 60% target. The holdings carry no lot tags, so who
    # owns which share is unknowable; the split is pro-rata to each sleeve's
    # TARGET weight on the ticker -- the same weights `all_sleeve_targets` sums
    # -- which makes per-sleeve over/under add up to the per-ticker drift card's.
    # Equal shares when every sleeve wants it at weight 0.
    per_sleeve: list[dict[str, float]] = []
    wanted: dict[str, list[float]] = {}
    for s in sleeves:
        targets = {tk.upper(): float(w or 0.0) for tk, w in (sleeve_targets(
            s.strategy_id, s.sleeve_pct, live=_live_for(live, s.strategy_id)) or {}).items()}
        per_sleeve.append(targets)
        for tk, w in targets.items():
            wanted.setdefault(tk, []).append(w)

    def _share(tk: str, w: float) -> float:
        ws = wanted.get(tk) or [w]
        tot = sum(ws)
        return (w / tot) if tot > 0 else 1.0 / len(ws)

    out = []
    counted: set[str] = set()
    for s, targets in zip(sleeves, per_sleeve):
        _sig = (live or {}).get(s.strategy_id) or {}
        by_tk = {tk: held.get(tk, 0.0) * _share(tk, w) for tk, w in targets.items()}
        held_ils = sum(by_tk.values())
        counted |= set(targets)
        shared_with = {tk: sorted(o.strategy_id for o, t in zip(sleeves, per_sleeve)
                                  if o is not s and tk in t)
                       for tk in targets if len(wanted.get(tk) or []) > 1}
        target_pct = round(float(s.sleeve_pct or 0.0), 4)
        held_pct = round(held_ils / nav, 4) if nav else 0.0
        out.append({
            "strategy_id": s.strategy_id,
            "target_pct": target_pct,
            # WHICH LEG, AND HOW OLD THE ANSWER IS. `tickers` below now follows
            # the rule rather than the catalogue, so the screen must be able to
            # say whether that came from a signal and when it was taken --
            # otherwise "your sleeve holds QQQ" is indistinguishable from a
            # default nobody produced. See `live_sleeve_targets` on why there is
            # no staleness gate.
            "following_signal": bool(_sig.get("target")),
            "signal_as_of": _sig.get("as_of") or None,
            "held_ils": round(held_ils, 2),
            # C15: what this sleeve holds of each ticker (shared tickers split as
            # above), so a trim can come from THIS sleeve's holdings only.
            "held_by_ticker_ils": {tk: round(v, 2) for tk, v in sorted(by_tk.items()) if v > 0},
            # C27: what this sleeve wants of each ticker (share of NAV), so a reader
            # can tell which part of a gap the app may actually buy.
            "target_by_ticker": {tk: round(w, 6) for tk, w in sorted(targets.items())},
            "held_pct": held_pct,
            # BOTH SIDES IN PERCENT. This read `target_pct - held_pct`, and
            # those are not the same unit: `target_pct` is 0-100 (33.09 means
            # 33.09%) while `held_pct` is a FRACTION (0.3509), a convention the
            # UI documents at index.html:1579. So a sleeve at 33.9% target
            # holding 35.09% of the book reported a gap of +33.54 -- an
            # under-funding of a third of the book -- when it is actually 1.2
            # points OVER. The number was wrong by the whole held fraction, and
            # always in the direction of "you hold nothing". PR 60.1.
            "gap_pct": round(target_pct - held_pct * 100.0, 4),
            "tickers": sorted(targets),
            # PR 131: the tickers this sleeve shares, and with whom.
            "shared_with": shared_with,
        })

    # The book total from the union of tickers. Since PR 131 the per-sleeve
    # figures add up to the same number (each shared ticker is split, not
    # counted in every sleeve); the union stays the source of truth.
    allocated_held_ils = sum(held.get(tk, 0.0) for tk in counted)
    allocated_held_pct = round(allocated_held_ils / nav, 4) if nav else 0.0
    return {
        "nav_ils": round(nav, 2),
        "sleeves": out,
        "allocated_pct": total_pct(sleeves),
        "allocated_held_pct": allocated_held_pct,
        "allocated_held_ils": round(allocated_held_ils, 2),
        # The core's ACTUAL share, which is not `remainder_pct` -- that is the
        # core's TARGET. On this book the two differ by the same 49 points.
        "core_held_pct": round(max(0.0, 1.0 - allocated_held_pct), 4) if nav else 0.0,
        "core_pct": remainder_pct(sleeves),
    }


# --------------------------------------------------------------------------- #
# Writing -- C2. Everything above this line existed in C1 and wrote nothing.
# --------------------------------------------------------------------------- #
async def add_or_resize(session: AsyncSession, user: User, strategy_id: str,
                        sleeve_pct: float) -> dict:
    """Add a sleeve, or re-size one already on the book. Never a second copy.

    Refuses rather than half-applies: over-allocating is returned as a reason the
    caller can print, in the same voice ``_fund_sleeve`` uses when it cannot
    fund. Nothing is written on a refusal.
    """
    sleeves = await list_sleeves(session, user)
    why = validate(sleeves, strategy_id=strategy_id, sleeve_pct=sleeve_pct,
                   replacing=True)
    if why:
        return {"ok": False, "error": "sleeve does not fit", "reason": why,
                "allocated_pct": total_pct(sleeves)}

    row = next((s for s in sleeves if s.strategy_id == strategy_id), None)
    if row is None:
        session.add(PlanSleeve(subject=user.email, strategy_id=strategy_id,
                               sleeve_pct=float(sleeve_pct), is_core=False))
        action, was = "added", None
    else:
        was = float(row.sleeve_pct or 0.0)
        row.sleeve_pct = float(sleeve_pct)
        action = "resized" if was != float(sleeve_pct) else "unchanged"
    await session.flush()
    after = await list_sleeves(session, user)
    return {"ok": True, "action": action, "strategy_id": strategy_id,
            "sleeve_pct": float(sleeve_pct), "previous_pct": was,
            "allocated_pct": total_pct(after), "core_pct": remainder_pct(after)}


async def remove(session: AsyncSession, user: User, strategy_id: str) -> dict:
    """Drop a sleeve. The caller is responsible for the caps -- see
    ``strategy_service.retire_sleeve``, which is the only thing that should call
    this, because a row removed without re-levelling the caps leaves a ceiling on
    the book with nothing behind it."""
    row = next((s for s in await list_sleeves(session, user)
                if s.strategy_id == strategy_id), None)
    if row is None:
        return {"ok": False, "error": f"'{strategy_id}' is not a sleeve on this book"}
    was = float(row.sleeve_pct or 0.0)
    await session.delete(row)
    await session.flush()
    after = await list_sleeves(session, user)
    return {"ok": True, "removed": strategy_id, "was_pct": was,
            "allocated_pct": total_pct(after), "core_pct": remainder_pct(after)}


# --------------------------------------------------------------------------- #
# The core -- C6
#
# One row, `is_core=True`, naming the static family that manages the remainder.
# `sleeve_pct` on it is always 0: the core's SIZE is still the computed
# remainder, and storing a second answer to "how big is the core" is how the two
# would eventually disagree.
# --------------------------------------------------------------------------- #
async def get_core(session: AsyncSession, user: User) -> PlanSleeve | None:
    """The core strategy row, or None for a book managed by its objective alone."""
    return (await session.scalars(
        select(PlanSleeve)
        .where(PlanSleeve.subject == user.email, PlanSleeve.is_core.is_(True))
        .order_by(PlanSleeve.created_at))).first()


async def core_strategy_id(session: AsyncSession, user: User) -> str | None:
    row = await get_core(session, user)
    return row.strategy_id if row is not None else None


def validate_core(strategy_id: str) -> str | None:
    """Why this strategy may not be the core, or None if it may.

    A rule-based strategy is refused here rather than coerced into a core. The
    two are different objects: a sleeve is a mechanical rule over a share of NAV
    with an entry and an exit, a core is a whole-book target mix. Letting
    ``btm_trend_soxl`` be a core would hand the book a target allocation derived
    from a 3x fund's model basket, which is the mistake ``sleeve_basket`` was
    written to prevent at the other end.
    """
    from app.services import strategies as static_cat
    from app.services import strategy_catalog

    sid = (strategy_id or "").strip()
    if not sid:
        return "a core needs a strategy"
    if strategy_catalog.get(sid) is not None:
        return (f"{sid} is a rule-based sleeve, not a core. It governs a share of "
                f"the book on a signal; a core is the whole-book mix the rest is "
                f"managed to. Add it as a sleeve instead")
    if static_cat.get(sid) is None:
        return f"'{sid}' is not a strategy this app knows"
    return None


async def set_core(session: AsyncSession, user: User, strategy_id: str) -> dict:
    """Name the strategy that manages the core. Replaces any previous choice.

    Never a second row: the core is singular by construction, so this updates in
    place rather than adding. Two is_core rows would give ``get_core`` an
    oldest-first answer that silently ignored the newer choice.
    """
    why = validate_core(strategy_id)
    if why:
        return {"ok": False, "error": "not a core strategy", "reason": why}

    row = await get_core(session, user)
    was = row.strategy_id if row is not None else None
    if row is None:
        session.add(PlanSleeve(subject=user.email, strategy_id=strategy_id,
                               sleeve_pct=0.0, is_core=True))
    else:
        row.strategy_id = strategy_id
        row.sleeve_pct = 0.0
    await session.flush()
    return {"ok": True, "action": "unchanged" if was == strategy_id
            else ("changed" if was else "set"),
            "strategy_id": strategy_id, "previous": was}


async def clear_core(session: AsyncSession, user: User) -> dict:
    """Back to a core managed by the objective alone. Sells nothing, like every
    other removal here -- it drops a target, not a holding."""
    row = await get_core(session, user)
    if row is None:
        return {"ok": False, "error": "no core strategy is set on this book"}
    was = row.strategy_id
    await session.delete(row)
    await session.flush()
    return {"ok": True, "removed": was}


# --------------------------------------------------------------------------- #
# Backfill -- REMOVED with the columns it read.
#
# `backfill_once` and `backfill_core_once` turned `plans.strategy` into sleeve
# and core rows. Both were one-shot, guarded by their own KVSetting key, and
# both have been spent on every live deploy -- production logs "0 sleeve(s)
# created" and "0 core row(s) created" on every boot. With the columns gone
# there is nothing left for them to read, and a migration helper that outlives
# its source is just an unreachable branch that still has to be understood.
#
# BACKFILL_KEY / CORE_BACKFILL_KEY are deliberately NOT deleted from the KV
# store. They are the record that the migration happened, and removing them
# would make a future re-introduction look like it had never run.
# --------------------------------------------------------------------------- #
