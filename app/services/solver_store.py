"""The solver's last answer, written down so something else can act on it.

T5's one sentence -- *"the solver becomes a constraint the Risk / Decision /
Adversary agents read; not an eighth agent, not the decision maker"* -- turned
out to be blocked on plumbing rather than on design. Nothing stored a verdict.
`GET /plan/target` recomputed from scratch every request: a coarse-then-fine
grid sweep, each point one simulation of ten years of daily closes. Meanwhile
the war room already costs **6,726 ms** on `/recommendations` while every other
agent comes in under 50 ms. Calling the solver from inside it would have put a
multi-second sweep on the slowest path in the app.

So the solver writes, and the war room reads. Two properties make that safe:

**Age travels with the answer.** A verdict is a statement about one book at one
moment, and this one is allowed to BLOCK a card. A reader that cannot say how
old it is has no business acting on it, so `constraint()` returns the age and
every card that acts on a verdict prints it.

**Stale stops blocking, it does not stop speaking.** Past `MAX_BLOCKING_AGE_H`
the constraint keeps its finding and drops its teeth: `blocks` goes False,
`reason` says why. The alternative -- letting a week-old verdict go on
disabling buttons -- is how an app starts refusing things for reasons nobody
can reconstruct.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import SolverVerdict, User

logger = logging.getLogger(__name__)

# Past this, a verdict still speaks but no longer blocks. Sized against the
# nightly job: a verdict older than ~1.5 nights means the job has missed a run,
# and a missed job is exactly when its output should stop being load-bearing.
MAX_BLOCKING_AGE_H = 36.0

# The outcomes that say "this book cannot get where it is aimed". REACHED and
# REACHED_ABOVE_CAP are not here on purpose -- REACHED_ABOVE_CAP is reachable,
# just not within the concentration cap, which is a different conversation and
# not one that should disable a button.
BLOCKING_OUTCOMES = frozenset({"UNREACHABLE", "DRAWDOWN_BOUND"})


async def record(session: AsyncSession, user: User, verdict: dict, *,
                 source: str = "on_demand") -> SolverVerdict | None:
    """Persist one solve. Best-effort: a failed write must never break a solve.

    NOT_MEASURABLE is stored too, deliberately. "The solver could not answer" is
    itself an answer a reader needs -- without the row, a book whose history is
    missing looks identical to a book nobody has ever solved, and the second
    deserves a nudge while the first deserves an explanation.
    """
    if not isinstance(verdict, dict) or not verdict.get("outcome"):
        return None
    try:
        floor = verdict.get("floor") or {}
        binding = verdict.get("binding_constraint") or {}
        target = verdict.get("target") or {}
        row = SolverVerdict(
            subject=user.email,
            solver_version=str(verdict.get("solver_version") or "")[:16],
            outcome=str(verdict["outcome"])[:32],
            target_excess_pct=_f(target.get("excess_pct")),
            max_drawdown_pct=_f(target.get("max_drawdown_pct")),
            solved_total_sleeve_pct=_f(verdict.get("solved_total_sleeve_pct")),
            floor_breaches_ceiling=bool(floor.get("breaches_ceiling")),
            binding_constraint=str(binding.get("kind") or "")[:32],
            source=source[:16],
            # Only what a constraint needs to EXPLAIN itself. The full result is
            # recomputed by /plan/target anyway; copying it here would make this
            # table a second answer to a question that already has one.
            detail={
                "floor": {"at_zero_sleeve_pct": floor.get("at_zero_sleeve_pct"),
                          "max_drawdown_pct": floor.get("max_drawdown_pct"),
                          "note": floor.get("note")},
                "binding_constraint": binding,
                "best_within_ceiling": verdict.get("best_within_ceiling"),
                "display_total_sleeve_pct": verdict.get("display_total_sleeve_pct"),
                "measured_max_drawdown_pct": (verdict.get("measured") or {}).get("max_drawdown_pct"),
                "excess_cagr_pct": (verdict.get("measured") or {}).get("excess_cagr_pct"),
                "degraded": verdict.get("degraded"),
                "reason": verdict.get("reason"),
                # Stored so a CARD can say "this ceiling is below what your book
                # already does" without paying for a solve. It is the field that
                # explains a week of "no" on this book, and a reader who has to
                # re-solve to see it will not see it.
                "ceiling_check": verdict.get("ceiling_check"),
            },
        )
        session.add(row)
        await session.commit()
        return row
    except Exception:  # noqa: BLE001 -- a solve that answered must not fail on its receipt
        logger.warning("could not record solver verdict", exc_info=False)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


# T6b writes into the SAME table -- one book, one place its measured verdicts
# live -- so every reader has to say which kind it wants. Excluded by the CORE
# version rather than selected by the target solver's own ("s1"), because
# SOLVER_VERSION is meant to be bumped and an `== "s1"` filter would silently
# stop finding rows the day it is.
CORE_VERSION = "t6b"
# PR 90's frontier verdicts share this table (see `frontier_service`). Named
# here so the target-solver reader can exclude them: a frontier row read as a
# target solve would be a constraint nobody set.
FRONTIER_VERSION = "frontier-f1"


async def latest(session: AsyncSession, user: User) -> SolverVerdict | None:
    """The newest TARGET-SOLVER verdict. Never a core-search row."""
    row = (await session.scalars(
        select(SolverVerdict)
        .where(SolverVerdict.subject == user.email)
        .where(SolverVerdict.solver_version != CORE_VERSION)
        .where(SolverVerdict.solver_version != FRONTIER_VERSION)
        .order_by(desc(SolverVerdict.created_at))
        .limit(1))).first()
    return row


async def latest_core(session: AsyncSession, user: User) -> SolverVerdict | None:
    """The newest T6b core search."""
    row = (await session.scalars(
        select(SolverVerdict)
        .where(SolverVerdict.subject == user.email)
        .where(SolverVerdict.solver_version == CORE_VERSION)
        .order_by(desc(SolverVerdict.created_at))
        .limit(1))).first()
    return row


async def record_core(session: AsyncSession, user: User, result: dict, *,
                      source: str = "on_demand") -> SolverVerdict | None:
    """Persist one core search. Best-effort, exactly like `record`.

    `detail` carries the verdict and the top rows and nothing else. The full
    ranking is recomputed by the route anyway; copying fifteen rows of two
    windows each would make this table a second answer to a question that
    already has one -- the failure migration 0019 removed one layer down.
    """
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    verdict = result.get("verdict") or {}
    if not verdict.get("kind"):
        return None
    try:
        row = SolverVerdict(
            subject=user.email,
            solver_version=CORE_VERSION,
            outcome=str(verdict["kind"])[:32],
            target_excess_pct=None,
            max_drawdown_pct=_f(result.get("max_drawdown_pct")),
            solved_total_sleeve_pct=_f(result.get("sleeves_held_pct")),
            floor_breaches_ceiling=False,
            binding_constraint="core_search",
            source=source[:16],
            detail={
                "verdict": verdict,
                "ranked_on": result.get("ranked_on"),
                "coverage": result.get("coverage"),
                "core_pct_of_book": result.get("core_pct_of_book"),
                "top": [{"id": r.get("id"), "name": r.get("name"),
                         "excess_pct": r.get("excess_pct"),
                         "max_drawdown_pct": r.get("max_drawdown_pct"),
                         "reference_max_drawdown_pct":
                             (r.get("reference") or {}).get("max_drawdown_pct"),
                         "admissible": r.get("admissible"),
                         "flags": r.get("flags")}
                        for r in (result.get("ranked") or [])[:5]],
            },
        )
        session.add(row)
        await session.commit()
        return row
    except Exception:  # noqa: BLE001 -- a search that answered must not fail on its receipt
        logger.warning("could not record core search", exc_info=False)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def as_core_finding(row: SolverVerdict | None) -> dict:
    """The small shape a CARD consults. Same contract as `as_constraint`."""
    if row is None:
        return {"known": False, "kind": None, "age_hours": None,
                "headline": None, "means": None, "shallowest_pct": None,
                "ceiling_pct": None, "none_admissible": False}
    age = _age_hours(row)
    v = (row.detail or {}).get("verdict") or {}
    return {
        "known": True,
        "kind": row.outcome,
        "age_hours": round(age, 1) if age is not None else None,
        "solved_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
        "headline": v.get("headline"),
        "means": v.get("means"),
        "shallowest_pct": v.get("shallowest_pct"),
        "ceiling_pct": row.max_drawdown_pct,
        "none_admissible": row.outcome == "NONE_ADMISSIBLE",
        "top": (row.detail or {}).get("top") or [],
        "source": row.source,
    }


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _age_hours(row: SolverVerdict) -> float | None:
    ts = getattr(row, "created_at", None)
    if not ts:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0)


def as_constraint(row: SolverVerdict | None) -> dict:
    """The shape the agents consult. Pure, so the rule is testable without a DB.

    Deliberately small. The agents get a decision and a sentence, not the
    solver's result dict -- an agent that can read the whole verdict will start
    re-deriving conclusions from it, and then there are two answers to "can this
    book get there" again.
    """
    if row is None:
        return {"known": False, "blocks": False, "outcome": None, "age_hours": None,
                "reason": "No solve on record for this book yet.",
                "headline": None, "solved_at": None, "stale": False}

    age = _age_hours(row)
    stale = age is not None and age > MAX_BLOCKING_AGE_H
    blocking_outcome = row.outcome in BLOCKING_OUTCOMES

    if not blocking_outcome:
        reason = f"The solver's last answer was {row.outcome}, which does not block anything."
    elif stale:
        # SPEAKS, DOES NOT BLOCK. A verdict this old means the nightly job has
        # missed a run; a missed job is exactly when its output should stop
        # being load-bearing.
        reason = (f"The solver said {row.outcome} {age:.0f}h ago. That is too old to hold a "
                  f"button shut, so it is shown and not enforced -- open the target card to "
                  f"re-solve.")
    else:
        reason = _blocking_reason(row, age)

    return {
        "known": True,
        "blocks": bool(blocking_outcome and not stale),
        "outcome": row.outcome,
        "age_hours": round(age, 1) if age is not None else None,
        "stale": stale,
        "solved_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
        "reason": reason,
        "headline": _headline(row),
        "required_sleeve_pct": row.solved_total_sleeve_pct,
        "ceiling_pct": row.max_drawdown_pct,
        # The core's OWN measured drawdown, at zero sleeves. `_headline` already
        # states it in prose; a card that has to pull it back out of a sentence
        # to put it in an audit trail is one rewording away from printing
        # nothing. This is not an agent re-deriving a conclusion -- it is the
        # conclusion's evidence, measured once and exposed once.
        "core_drawdown_pct": ((row.detail or {}).get("floor") or {}).get("max_drawdown_pct"),
        # Whether the CEILING ITSELF is the thing that binds. Small on purpose,
        # like everything else here: three facts and a sentence, not the block.
        "ceiling_check": _ceiling_view((row.detail or {}).get("ceiling_check")),
        "floor_breaches_ceiling": bool(row.floor_breaches_ceiling),
        "binding_constraint": row.binding_constraint or None,
        "source": row.source,
        "solver_version": row.solver_version,
    }


def _ceiling_view(cc: dict | None) -> dict:
    """The three facts a card needs about the ceiling, and the sentence.

    Absent on any verdict solved before PR 14, so `known` is False rather than
    the fields being None -- "we did not check" and "checked, nothing wrong" are
    different claims and a card must not render them the same way.
    """
    if not isinstance(cc, dict) or cc.get("book_measured_pct") is None:
        return {"known": False, "book_already_breaches": False,
                "ceiling_pct": None, "book_measured_pct": None,
                "gap_pct": None, "note": None}
    return {"known": True,
            "book_already_breaches": bool(cc.get("book_already_breaches")),
            "ceiling_pct": cc.get("ceiling_pct"),
            "book_measured_pct": cc.get("book_measured_pct"),
            "gap_pct": cc.get("gap_pct"),
            "note": cc.get("note")}


def _headline(row: SolverVerdict) -> str:
    """One sentence, and the core case gets its own because it is different in kind."""
    if row.floor_breaches_ceiling:
        # No sleeve size fixes this, INCLUDING ZERO. Saying "reduce your sleeves"
        # here would be advice that cannot work.
        floor = (row.detail or {}).get("floor") or {}
        at_zero = floor.get("max_drawdown_pct")
        return ("Your core alone breaches your drawdown ceiling"
                + (f" ({at_zero:.1f}% with no sleeves at all against a "
                   f"{row.max_drawdown_pct:.1f}% ceiling)" if at_zero is not None
                      and row.max_drawdown_pct is not None else "")
                + " — no sleeve size repairs that, including none at all.")
    if row.outcome == "DRAWDOWN_BOUND":
        return ("Your return target is only reachable by breaching your drawdown ceiling.")
    if row.outcome == "UNREACHABLE":
        return ("The solver could not find any sleeve size that reaches your return target.")
    if row.outcome == "REACHED_ABOVE_CAP":
        return ("Your target is reachable, but only past your concentration cap.")
    if row.outcome == "NOT_MEASURABLE":
        return ("The solver could not measure this book.")
    return "Your target is reachable at the sizes you run."


def _blocking_reason(row: SolverVerdict, age: float | None) -> str:
    when = f" (solved {age:.0f}h ago)" if age is not None else ""
    if row.floor_breaches_ceiling:
        return (f"The solver says the core alone breaches your ceiling{when}, so adding risk "
                f"here cannot be justified by the plan — change the core or raise the ceiling "
                f"first.")
    if row.outcome == "DRAWDOWN_BOUND":
        return (f"The solver says your target is only reachable past your drawdown "
                f"ceiling{when}. Taking more risk moves you further from the plan, not toward "
                f"it.")
    return (f"The solver says your target is unreachable at any sleeve size{when}. More risk "
            f"does not close a gap the measurement says is not there.")


# --------------------------------------------------------------------------- #
# The nightly re-solve
# --------------------------------------------------------------------------- #
async def refresh_all(session: AsyncSession) -> dict:
    """Re-answer the question each book last asked, against today's prices.

    IT DOES NOT INVENT A QUESTION. A solve needs a target excess and a drawdown
    ceiling, and neither is stored on the plan -- the UI supplies them as query
    parameters, so they exist only in the answer. This job therefore re-runs the
    LAST verdict's own parameters. A user who has never opened the target card
    has no stored question, gets no verdict, and their war room says so ("no
    solve on record") rather than being handed a ceiling nobody chose.

    That is deliberate. A constraint strong enough to disable a button must come
    from a number the user actually set.
    """
    from sqlalchemy import distinct

    from app.services import target_solver

    subjects = list((await session.scalars(select(distinct(SolverVerdict.subject)))).all())
    done, skipped, failed, cores = 0, 0, 0, 0
    for subject in subjects:
        user = (await session.scalars(select(User).where(User.email == subject))).first()
        if user is None:
            skipped += 1
            continue
        prev = await latest(session, user)
        if prev is None or prev.target_excess_pct is None or not prev.max_drawdown_pct:
            skipped += 1
            continue
        try:
            out = await target_solver.solve_for(
                session, user,
                target_excess_pct=float(prev.target_excess_pct),
                max_drawdown_pct=float(prev.max_drawdown_pct))
            # `solve_for` records with source="on_demand"; this run was not.
            # Overwrite rather than write a second row, so the age a card
            # reports is the age of the newest solve and its provenance is
            # honest about which of the two produced it.
            row = await latest(session, user)
            if row is not None and out.get("outcome"):
                row.source = "scheduled"
                await session.commit()
            done += 1
        except Exception:  # noqa: BLE001 -- one bad book must not stop the rest
            logger.warning("scheduled re-solve failed for a subject", exc_info=False)
            failed += 1

        # T6b, against the SAME ceiling the target solve just used, in the same
        # pass. Two searches whose answers are read side by side on one card
        # must not be able to be about two different ceilings -- and the card
        # that says "no core repairs this" is only true of the ceiling it was
        # measured against.
        #
        # Its own try. A core search that fails must not undo a target solve
        # that succeeded: they answer different questions and the second is not
        # a precondition of the first.
        try:
            found = await target_solver.solve_core_for(
                session, user, max_drawdown_pct=float(prev.max_drawdown_pct))
            if found.get("ok"):
                await record_core(session, user, found, source="scheduled")
                cores += 1
        except Exception:  # noqa: BLE001
            logger.warning("scheduled core search failed for a subject", exc_info=False)
    return {"solved": done, "skipped": skipped, "failed": failed, "cores": cores}


async def _refresh_job() -> dict:
    import asyncio  # noqa: F401  (imported for symmetry with the sync wrapper below)

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings

    # ITS OWN ENGINE, NullPool, INSIDE THIS LOOP. APScheduler runs this in a
    # worker thread via `asyncio.run`, and handing that loop a connection from
    # the app's shared pool is the cross-loop asyncpg rejection CLAUDE.md warns
    # about -- green on SQLite, dead in production. It has already cost this
    # repo three incidents.
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            out = await refresh_all(session)
            # PR 90: the frontier verdict, same nightly pass. Its own per-book
            # try lives inside; a frontier failure never undoes a target solve.
            from app.services import frontier_service
            out.update(await frontier_service.refresh_all(session))
            return out
    finally:
        await engine.dispose()


def run_solver_refresh_blocking() -> dict:
    """Sync entrypoint for APScheduler (runs in its own thread)."""
    import asyncio

    # NO try/except HERE, and it is the same correction PR 12 made to
    # pricing_service. Returning {"solved": 0, "skipped": 0, "failed": 0} from a
    # crash is the exact shape of a run that found no book with a stored
    # question -- the common, healthy case. So a broken nightly re-solve would
    # have read as a quiet night, forever. `scheduler._guarded` is the one place
    # that records an outcome; two recorders is two versions of whether the job
    # ran. Written in PR 11 by me, with the older bug already in front of me.
    res = asyncio.run(_refresh_job())
    logger.info("solver refresh: %s", res)
    return res
