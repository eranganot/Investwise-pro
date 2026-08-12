"""The book's sleeves: which strategies it runs, and how much of it each governs.

This is the read half of "core + N sleeves". Everything here is deliberately
inert with respect to the running app: nothing in this module arms a rule, funds
a leg, sizes a trade or writes to ``plans``. The one write it does perform is a
one-shot backfill of the single strategy the ``plans`` columns can already hold,
so the new table starts out agreeing with the old one instead of reading empty
for someone who has a strategy applied.

**The core is the implicit remainder.** Sleeves sum to <= 100 and the rest of the
book stays objective-managed exactly as it is today. That is the smaller of the
two models on the table -- the other makes the core an explicit ``is_core`` row
so the sleeves sum to exactly 100 -- and the column for it exists but is never
written, so choosing it later costs a behaviour change rather than a migration.

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


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
async def list_sleeves(session: AsyncSession, user: User) -> list[PlanSleeve]:
    """Every sleeve on this book, oldest first so the order is stable."""
    return list((await session.scalars(
        select(PlanSleeve)
        .where(PlanSleeve.subject == user.email)
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
    return [{"strategy_id": s.strategy_id,
             "sleeve_pct": round(float(s.sleeve_pct or 0.0), 4),
             "is_core": bool(s.is_core)} for s in sleeves]


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
