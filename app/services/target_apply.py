"""Phase A - accepting the solver's answer, against the tracked book only.

This is the first thing in the T line that WRITES. T0-T5 were read-only without
exception, and the reason that rule existed is worth restating now that it is
being relaxed rather than after: a return target one tap from a book change is
the C5 slider bug with higher stakes.

So the relaxation is narrow, and it stops at a hard line:

    **Nothing here places an order.** It writes `plan_sleeves` -- the TRACKED
    book, a set of intended percentages -- and nothing else. No broker, no
    quantity, no price. `investing-discipline` section 5 is the boundary, and
    this module sits entirely on the safe side of it.

Three failure modes decide the design. Each is a real way a naive apply goes
wrong, and each is handled BEFORE any row is touched:

1.  **Half-applied writes.** Going from (40, 40) to (70, 20) by calling
    `add_or_resize` in the order the solve listed them raises the first sleeve
    to 70% while the second still holds 40%. That is 110%, `validate` refuses
    it, and the book is left at (40, 20) -- neither the old plan nor the new
    one, and no error the user asked for. `plan_apply` orders every DECREASE
    before every INCREASE so no intermediate state can exceed the final one.

2.  **Applying a plan solved against a different book.** The solve carries the
    sizes it measured from. If a sleeve moved between solving and accepting --
    another tab, another session, a funding run -- the recommendation is about a
    book that no longer exists. `apply_target` compares every `from_pct` against
    the live row and refuses on any mismatch rather than writing a plan whose
    premise is stale.

3.  **A write with no way back.** Every application records the state it
    replaced, so `undo` is a read of the last row rather than the user
    remembering two numbers.

The planning half is pure and takes plain data, so the ordering rule is testable
without a database.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

APPLY_VERSION = "a1"

# `sleeve_service.validate` allows 0.05 of slack; the same figure is used here so
# a plan this module calls admissible is never refused one layer down.
EPSILON_PCT = 0.05


# --------------------------------------------------------------------------
# the planning half -- pure, no I/O, no ORM
# --------------------------------------------------------------------------

def plan_apply(resizes: list[dict], *, cash_floor_pct: float = 0.0) -> dict:
    """Turn the solve's `would_execute.resizes` into an ordered write plan.

    `resizes` is `[{"strategy_id", "from_pct", "to_pct"}]`, exactly as
    `target_solver._would_execute` emits it.

    Returns `{"ok": True, "steps": [...], ...}` or `{"ok": False, "reason": ...}`.
    A refusal means NOTHING should be written -- there is no partial plan.
    """
    rows = []
    for r in resizes or []:
        sid = str(r.get("strategy_id") or "").strip()
        if not sid:
            return {"ok": False, "reason": "a resize with no strategy_id",
                    "detail": "the solve returned a step this book cannot identify"}
        try:
            frm = round(float(r.get("from_pct") or 0.0), 2)
            to = round(float(r.get("to_pct") or 0.0), 2)
        except (TypeError, ValueError):
            return {"ok": False, "reason": f"{sid} has a size that is not a number"}
        if to < 0:
            return {"ok": False, "reason": f"{sid} would go negative ({to}%)"}
        rows.append({"strategy_id": sid, "from_pct": frm, "to_pct": to})

    if not rows:
        return {"ok": False, "reason": "nothing to apply",
                "detail": "the solve produced no sleeve changes"}

    seen = set()
    for r in rows:
        if r["strategy_id"] in seen:
            return {"ok": False, "reason": f"{r['strategy_id']} appears twice in one plan"}
        seen.add(r["strategy_id"])

    # The FINAL state is validated first, as a whole. Checking each step against
    # the book as it stands would pass a plan whose end state does not fit, and
    # discover that only partway through writing it.
    ceiling = 100.0 - max(0.0, float(cash_floor_pct))
    final_total = round(sum(r["to_pct"] for r in rows), 2)
    if final_total > ceiling + EPSILON_PCT:
        return {"ok": False, "reason": "that plan does not fit the book",
                "detail": (f"the sleeves would total {final_total:g}% against a "
                           f"{ceiling:g}% ceiling (100% less a {cash_floor_pct:g}% "
                           f"cash floor). Nothing was written."),
                "final_total_pct": final_total, "ceiling_pct": round(ceiling, 2)}

    # A sleeve going to 0 is a REMOVAL, not a resize: sleeve_service.validate
    # refuses 0 outright ("0% is a removal, not a size"), so routing it through
    # add_or_resize would fail the whole apply for a step that is expressible.
    for r in rows:
        r["direction"] = ("down" if r["to_pct"] < r["from_pct"] - 1e-9
                          else "up" if r["to_pct"] > r["from_pct"] + 1e-9
                          else "same")
        r["op"] = "remove" if r["to_pct"] <= 0 else "resize"

    # THE ordering rule. Decreases and removals first, then no-ops (dropped),
    # then increases -- smallest increase first so the book grows monotonically
    # toward the final total and never passes above it on the way.
    downs = sorted([r for r in rows if r["direction"] == "down"],
                   key=lambda r: r["to_pct"])
    ups = sorted([r for r in rows if r["direction"] == "up"],
                 key=lambda r: r["to_pct"])
    steps = downs + ups

    # Prove the ordering actually holds, rather than trusting that it does. This
    # walks the plan and records the running total after each step; if any
    # intermediate total exceeds the ceiling the plan is refused here, in a pure
    # function, instead of half-way through a transaction.
    running = round(sum(r["from_pct"] for r in rows), 2)
    for s in steps:
        running = round(running - s["from_pct"] + s["to_pct"], 2)
        s["book_total_after_pct"] = running
        if running > ceiling + EPSILON_PCT:
            return {"ok": False, "reason": "no order of these changes fits the book",
                    "detail": (f"applying {s['strategy_id']} would put the sleeves at "
                               f"{running:g}% against a {ceiling:g}% ceiling. "
                               f"Nothing was written."),
                    "final_total_pct": final_total}

    return {"ok": True,
            "steps": steps,
            "changed": [s for s in steps if s["direction"] != "same"],
            "unchanged": [r["strategy_id"] for r in rows if r["direction"] == "same"],
            "final_total_pct": final_total,
            "ceiling_pct": round(ceiling, 2),
            "core_pct_after": round(100.0 - final_total, 2),
            "apply_version": APPLY_VERSION}


def stale_against(resizes: list[dict], live: dict[str, float]) -> list[dict]:
    """Which steps were solved against a size the book no longer holds.

    `live` is `{strategy_id: sleeve_pct}` as the book stands NOW. Returns the
    mismatches, empty when the plan's premise still holds.

    This is the guard for the quietest way an apply goes wrong: the numbers on
    the card are a measurement of a book that has since changed, so applying
    them is not "accepting the recommendation", it is overwriting the current
    plan with one computed for a different one. No exception would be raised and
    the resulting sizes would look entirely reasonable.
    """
    out = []
    for r in resizes or []:
        sid = str(r.get("strategy_id") or "")
        want = round(float(r.get("from_pct") or 0.0), 2)
        now = round(float(live.get(sid, 0.0)), 2)
        if abs(want - now) > EPSILON_PCT:
            out.append({"strategy_id": sid, "solved_against_pct": want,
                        "book_now_pct": now})
    return out


# --------------------------------------------------------------------------
# the writing half
# --------------------------------------------------------------------------

async def apply_target(session, user, *, resizes: list[dict],
                       context: dict | None = None,
                       confirm: bool = False) -> dict:
    """Write the solved sizes to `plan_sleeves`. Nothing else. No order is placed.

    The caller commits. Every refusal below returns before any row is touched,
    so an uncommitted session is an unchanged book.
    """
    from app.services import sleeve_service as sv
    from app.services.funding_service import cash_floor_pct

    if not confirm:
        # An explicit confirm, required at the API boundary. A write endpoint
        # that fires on a bare POST is one a stray retry or a double-tap can
        # trigger, and this one changes the user's plan.
        return {"ok": False, "reason": "not confirmed",
                "detail": "pass confirm=true to apply. Nothing was written."}

    before_rows = await sv.list_sleeves(session, user)
    before = {s.strategy_id: round(float(s.sleeve_pct or 0.0), 2)
              for s in before_rows if not getattr(s, "is_core", False)}

    stale = stale_against(resizes, before)
    if stale:
        return {"ok": False, "reason": "the book changed since this was solved",
                "detail": ("this recommendation was measured against sizes the book "
                           "no longer holds, so applying it would overwrite the "
                           "current plan rather than accept the one you read. "
                           "Re-solve, then accept. Nothing was written."),
                "stale": stale, "before": before}

    try:
        floor = float(await cash_floor_pct(session, user)) * 100.0
    except Exception:  # noqa: BLE001
        floor = 0.0

    plan = plan_apply(resizes, cash_floor_pct=floor)
    if not plan.get("ok"):
        return plan

    applied = []
    for s in plan["steps"]:
        if s["direction"] == "same":
            continue
        if s["op"] == "remove":
            res = await sv.remove(session, user, s["strategy_id"])
        else:
            res = await sv.add_or_resize(session, user, s["strategy_id"], s["to_pct"])
        if not res.get("ok"):
            # Refused mid-plan. The caller must NOT commit; returning ok=False
            # with the steps already flushed is what makes that unambiguous.
            return {"ok": False, "reason": "a step was refused, nothing is committed",
                    "detail": res.get("reason") or res.get("error"),
                    "failed_at": s["strategy_id"], "applied_before_failure": applied,
                    "rollback": "the caller must not commit this session"}
        applied.append({"strategy_id": s["strategy_id"],
                        "from_pct": s["from_pct"], "to_pct": s["to_pct"],
                        "action": res.get("action") or res.get("removed")})

    after_rows = await sv.list_sleeves(session, user)
    after = {s.strategy_id: round(float(s.sleeve_pct or 0.0), 2)
             for s in after_rows if not getattr(s, "is_core", False)}

    await _record(session, user, action="apply", before=before, after=after,
                  context=context, plan=plan)

    return {"ok": True, "applied": applied, "before": before, "after": after,
            "allocated_pct": round(sum(after.values()), 2),
            "core_pct": round(100.0 - sum(after.values()), 2),
            "apply_version": APPLY_VERSION,
            # Said on every successful write, because the one thing a user must
            # never infer from "applied" is that something was bought.
            "note": ("the tracked book was updated. No brokerage order was placed "
                     "and nothing was bought or sold.")}


async def undo_last(session, user, *, confirm: bool = False) -> dict:
    """Restore the sizes the last apply replaced."""
    from sqlalchemy import select

    from app.models.tables import PlanApplication

    if not confirm:
        return {"ok": False, "reason": "not confirmed",
                "detail": "pass confirm=true to undo. Nothing was written."}

    row = (await session.execute(
        select(PlanApplication)
        .where(PlanApplication.subject == user.email,
               PlanApplication.action == "apply")
        .order_by(PlanApplication.created_at.desc()).limit(1))).scalars().first()
    if row is None:
        return {"ok": False, "reason": "nothing to undo",
                "detail": "no application has been recorded for this book"}

    before = dict(row.before_state or {})
    after = dict(row.after_state or {})
    if not before and not after:
        return {"ok": False, "reason": "the recorded application is empty"}

    # Rebuilt as a resize list so the undo goes through the SAME ordering and
    # validation the apply did. An undo that writes directly would be a second
    # implementation of the rule that keeps the book consistent, and the two
    # would eventually disagree.
    resizes = [{"strategy_id": sid,
                "from_pct": after.get(sid, 0.0),
                "to_pct": before.get(sid, 0.0)}
               for sid in sorted(set(before) | set(after))]

    res = await apply_target(session, user, resizes=resizes, confirm=True,
                             context={"kind": "undo", "of": str(row.id)})
    if not res.get("ok"):
        return res
    await _record(session, user, action="undo", before=res["before"],
                  after=res["after"], context={"of": str(row.id)}, plan=None)
    res["undone"] = str(row.id)
    res["note"] = "the tracked book was restored. No brokerage order was placed."
    return res


async def _record(session, user, *, action: str, before: dict, after: dict,
                  context: dict | None, plan: dict | None) -> None:
    """Audit row. Best-effort: losing the log must not cost the write."""
    try:
        from app.models.tables import PlanApplication
        session.add(PlanApplication(
            subject=user.email, action=action,
            before_state=before, after_state=after,
            context=dict(context or {}),
            allocated_pct_after=round(sum(after.values()), 2),
            apply_version=APPLY_VERSION,
            note=(plan or {}).get("reason") or "",
        ))
        await session.flush()
    except Exception:  # noqa: BLE001
        logger.warning("plan application not recorded (%s)", action, exc_info=False)
