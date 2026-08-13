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

from app.models.tables import KVSetting, Plan, PlanSleeve, User

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

    out: dict[str, float] = {}
    for s in await list_sleeves(session, user):
        for tk, w in (sleeve_targets(s.strategy_id, s.sleeve_pct) or {}).items():
            tk = tk.upper()
            out[tk] = out.get(tk, 0.0) + float(w)
    return out


async def sleeve_tickers(session: AsyncSession, user: User) -> set[str]:
    """Just the names. The exclusion set, for callers that do not need weights."""
    return set(await all_sleeve_targets(session, user))


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
# Backfill
# --------------------------------------------------------------------------- #
async def backfill_once(session: AsyncSession) -> dict:
    """Turn each ``plans.strategy`` into one sleeve row. Runs exactly once, ever.

    Called at startup rather than from the migration: the ids are UUIDs and the
    subject is ``users.email``, so doing this in cross-dialect SQL means
    generating keys and joining in a way that differs per backend. Running
    alembic by hand against this deploy has also failed twice, so the startup
    path is the one that actually executes.

    The old ``plans`` columns are left in place and still authoritative for
    everything that reads them -- this release changes no behaviour, it only
    makes the new table agree with the old one.
    """
    if await session.get(KVSetting, BACKFILL_KEY) is not None:
        return {"ran": False, "reason": "already backfilled"}

    rows = (await session.execute(
        select(Plan, User.email).join(User, User.id == Plan.user_id)
        .where(Plan.strategy.isnot(None)))).all()

    existing = {(s.subject, s.strategy_id) for s in
                (await session.scalars(select(PlanSleeve))).all()}

    created = 0
    for plan, email in rows:
        sid = (plan.strategy or "").strip()
        if not sid or not email or (email, sid) in existing:
            continue
        # A plan carrying a strategy but no size predates 0012. Falling back to
        # the catalog's suggested sleeve rather than to 100% is the same refusal
        # `sleeve_basket` makes: putting a whole book into a 3x fund because the
        # model basket says so in isolation is not a default anyone wants.
        pct = plan.strategy_sleeve_pct
        if pct is None:
            from app.services import strategy_catalog
            pct = (strategy_catalog.get(sid) or {}).get("sleeve_pct")
        if pct is None:
            # A static family, not a sleeve. It governs the book through the
            # objective, and inventing a percentage for it would put a number
            # on a card that nobody chose.
            continue
        session.add(PlanSleeve(subject=email, strategy_id=sid,
                               sleeve_pct=float(pct), is_core=False))
        existing.add((email, sid))
        created += 1

    session.add(KVSetting(key=BACKFILL_KEY, value=str(created)))
    await session.commit()
    logger.info("plan_sleeves backfill: %s sleeve(s) created", created)
    return {"ran": True, "created": created}


async def backfill_core_once(session: AsyncSession) -> dict:
    """The other half of C1's backfill: a ``plans.strategy`` naming a STATIC
    family becomes the core row. Runs exactly once, ever.

    C1's backfill skipped these on purpose -- it had nowhere to put them, since
    a static family has no sleeve size and inventing one would have put a number
    on a card nobody chose. C6 gives them a home.

    **Why a one-shot and not a read-through fallback.** The tempting version is
    "no core row? then read ``plans.strategy``" -- no migration, no key, works
    for every existing book. It breaks the moment C6 adds *clear the core*:
    ``plans.strategy`` still names the family, so the next page load hands the
    choice straight back. That is the same trap the sleeve backfill's own comment
    describes, and it is worth paying a second KV key not to fall into it twice.
    """
    if await session.get(KVSetting, CORE_BACKFILL_KEY) is not None:
        return {"ran": False, "reason": "already backfilled"}

    from app.services import strategies as static_cat

    have_core = {r.subject for r in (await session.scalars(
        select(PlanSleeve).where(PlanSleeve.is_core.is_(True)))).all()}

    rows = (await session.execute(
        select(Plan, User.email).join(User, User.id == Plan.user_id)
        .where(Plan.strategy.isnot(None)))).all()

    created = 0
    for plan, email in rows:
        sid = (plan.strategy or "").strip()
        if not sid or not email or email in have_core:
            continue
        # Only a static family. A rule-based id here means the last thing applied
        # was a sleeve, which says nothing about the core -- that ambiguity in
        # `plans.strategy` is exactly what C6 exists to remove.
        if static_cat.get(sid) is None:
            continue
        session.add(PlanSleeve(subject=email, strategy_id=sid,
                               sleeve_pct=0.0, is_core=True))
        have_core.add(email)
        created += 1

    session.add(KVSetting(key=CORE_BACKFILL_KEY, value=str(created)))
    await session.commit()
    logger.info("plan_sleeves core backfill: %s core row(s) created", created)
    return {"ran": True, "created": created}
