"""PR 136a (wave 5): the best book the app can measure for the goal, goal unchanged.

The sleeve plan's wave 5 (owner-approved; design 2026-10-18 in SLEEVE_OVERLAP_PLAN_2026-10-14.md):

**Step A -- which sleeves, what sizes**, scored by the goal model with the rules as armed. From the book as it is,
each step tries adding every catalogue strategy (never a same-bet look-alike of what is held, never Explore-only)
and removing each sleeve. Every candidate is scored beside the current book in ONE solve, so both read the same
price window (PR 135c). The best candidate is then re-sized by the goal model's own proposal and re-checked. A step
is kept only when it raises the goal chance by `optimizer_min_step_pts` or more and the chance of halving stays
within `halve_limit_pct` (or no higher than today's, when today's is already over it). At most `MAX_STEPS` steps.

**Step B -- protection and core**: the best book through the full solve -- the protection retune against the halving
limit and the core choice -- reported beside it (their own cards act on them after the next solve).

**An empty book** (a new user) starts all core -- the user's own core, read at run time, never a fixed mix (owner) --
and the search only adds. A "%/yr" goal is scored on a nominal amount (its chance does not depend on the amount).

Nightly (job `book_optimizer`, 05:10) and on demand; stored per user in `kv_settings`. Read-only on the book.

Not financial advice.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("investwise")

KV_PREFIX = "book_optimizer:"
MAX_STEPS = 3
MIN_STEP_PTS = 0.5                 # default; the setting is `optimizer_min_step_pts`
NOMINAL_NAV = 10_000.0             # an empty book's stand-in amount (a %/yr goal is scale-free)


def _min_step() -> float:
    from app.services import thresholds
    return float(thresholds.get("optimizer_min_step_pts", MIN_STEP_PTS))


def _cat():
    from app.services import strategy_catalog
    return strategy_catalog


def candidates(current: dict[str, float], corr: dict | None, *, cash_floor_pct: float = 0.0) -> list[dict]:
    """One step's candidate books. Pure. Each: {kind, sleeve, book}."""
    from app.services import sleeve_diversity as div
    cat = _cat()
    out = []
    room = 100.0 - sum(current.values()) - cash_floor_pct
    for sid in cat.active_ids():
        e = cat.get(sid) or {}
        if sid in current or e.get("explore_only"):
            continue
        if div.same_bets_for(sid, list(current), corr):
            continue
        size = min(float(e.get("sleeve_pct") or 0.0), room)
        if size < 1.0:
            continue
        out.append({"kind": "add", "sleeve": sid, "book": {**current, sid: size}})
    for sid in current:
        out.append({"kind": "remove", "sleeve": sid, "book": {k: v for k, v in current.items() if k != sid}})
    return out


def _ok_halve(after: float | None, before: float | None, limit: float) -> bool:
    if after is None:
        return False
    return after <= limit or (before is not None and after <= before)


async def optimize(session, user) -> dict:
    """Run steps A and B. Read-only."""
    from app.services import frontier_service as fs
    from app.services import sleeve_diversity as div
    from app.services import sleeve_service as sv
    from app.services.intake_service import list_positions
    from app.services.plan_service import get_plan
    from app.services.strategy_service import _snapshot

    plan = await get_plan(session, user)
    if plan is None or getattr(plan, "target_roi_pct", None) is None:
        return {"ok": False, "reason": "NO_GOAL",
                "detail": "the goal model needs a yearly return target and a deadline on the plan"}
    held = {s.strategy_id: float(s.sleeve_pct or 0.0) for s in await sv.list_sleeves(session, user)}
    nav = float(_snapshot(await list_positions(session, user)).get("nav") or 0.0)
    nominal = NOMINAL_NAV if nav <= 0 else None
    limit = float(fs.settings_of(plan)["halve_limit_pct"])
    corr = await div.load_correlations(session)
    min_step = _min_step()

    async def pair(cur: dict, cand: dict, adds: list[str]) -> tuple[dict, dict] | None:
        res = await fs.score_books(session, user, [cur, cand], extra_sleeves=adds or None, nominal_nav=nominal)
        if not res.get("ok") or not all(s.get("ok") for s in res["scores"]):
            return None
        return res["scores"][0], res["scores"][1]

    current = dict(held)
    start = await fs.score_books(session, user, [current], nominal_nav=nominal)
    if not start.get("ok") or not start["scores"][0].get("ok"):
        return {"ok": False, "reason": start.get("reason") or start["scores"][0].get("reason")}
    steps, tried = [], 0
    for _ in range(MAX_STEPS):
        best = None
        for c in candidates(current, corr):
            adds = [s for s in c["book"] if s not in held]
            got = await pair(current, c["book"], adds)
            tried += 1
            if not got:
                continue
            b, a = got
            gain = float(a["p_goal_pct"]) - float(b["p_goal_pct"])
            if gain >= min_step and _ok_halve(a.get("p_halve_pct"), b.get("p_halve_pct"), limit):
                if best is None or gain > best["gain"]:
                    best = {**c, "gain": gain, "adds": adds, "after": a, "before": b}
        if best is None:
            break
        # Re-size the winning set the way the goal model would, and keep that if it is at least as good.
        book = best["book"]
        prop = {k: float(v) for k, v in (best["after"].get("proposed") or {}).items() if k in book}
        if prop and prop != book:
            got = await pair(current, prop, best["adds"])
            if got and float(got[1]["p_goal_pct"]) >= float(best["after"]["p_goal_pct"]) \
                    and _ok_halve(got[1].get("p_halve_pct"), got[0].get("p_halve_pct"), limit):
                book, best["after"] = prop, got[1]
        steps.append({"kind": best["kind"], "sleeve": best["sleeve"], "book": book,
                      "goal_pts": round(float(best["after"]["p_goal_pct"]) - float(best["before"]["p_goal_pct"]), 2),
                      "after": {k: best["after"].get(k) for k in ("p_goal_pct", "p_halve_pct")}})
        current = book

    # The whole change in one solve (one window), and step B on the result.
    adds = [s for s in current if s not in held]
    final = await fs.score_books(session, user, [held, current], extra_sleeves=adds or None,
                                 nominal_nav=nominal, full=True)
    fin_before, fin_after = (final["scores"] if final.get("ok") and len(final["scores"]) == 2 else (None, None))
    keys = ("p_goal_pct", "p_halve_pct", "median", "growth_cagr_pct")
    return {"ok": True, "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "starter": nav <= 0, "nominal_nav": nominal, "held": held, "best": current,
            "halve_limit_pct": limit, "min_step_pts": min_step, "candidates_tried": tried,
            "steps": steps,
            "before": {k: (fin_before or start["scores"][0]).get(k) for k in keys},
            "after": {k: fin_after.get(k) for k in keys} if fin_after and fin_after.get("ok") else None,
            "protection": (fin_after or {}).get("protection"), "core": (fin_after or {}).get("core"),
            "converged": len(steps) < MAX_STEPS}


async def load(session, user) -> dict:
    from app.models.tables import KVSetting
    row = await session.get(KVSetting, KV_PREFIX + user.email)
    if row is None:
        return {}
    try:
        return json.loads(row.value or "{}")
    except ValueError:
        return {}


async def refresh(session, user) -> dict:
    from app.models.tables import KVSetting
    out = await optimize(session, user)
    if not out.get("ok"):
        return {**out, "stored": False}
    key = KV_PREFIX + user.email
    row = await session.get(KVSetting, key)
    if row is None:
        session.add(KVSetting(key=key, value=json.dumps(out)))
    else:
        row.value = json.dumps(out)
    await session.commit()
    return {**out, "stored": True}


async def refresh_all(session) -> dict:
    """Nightly: every user with a plan goal. One bad book never stops the rest."""
    from sqlalchemy import select

    from app.models.tables import Plan, User
    users = list((await session.scalars(
        select(User).join(Plan, Plan.user_id == User.id).where(Plan.target_roi_pct.is_not(None)))).all())
    done = failed = 0
    for user in users:
        try:
            r = await refresh(session, user)
            done += 1 if r.get("stored") else 0
            failed += 0 if r.get("stored") else 1
        except Exception:  # noqa: BLE001
            logger.warning("book optimizer failed for a user", exc_info=False)
            failed += 1
    return {"optimizer_stored": done, "optimizer_failed": failed}


async def _job() -> dict:
    # Own NullPool engine in this loop (CLAUDE.md, "Cross-loop sessions").
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await refresh_all(session)
    finally:
        await engine.dispose()


def run_book_optimizer_blocking() -> dict:
    """APScheduler entrypoint. RAISES when every user failed, so `_guarded` records it."""
    import asyncio
    res = asyncio.run(_job())
    if res["optimizer_failed"] and not res["optimizer_stored"]:
        raise RuntimeError(f"book optimizer: every user failed ({res['optimizer_failed']})")
    logger.info("book optimizer: %s", res)
    return res
