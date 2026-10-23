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
    keys = ("p_goal_pct", "p_halve_pct", "median", "growth_cagr_pct", "p_goal_without_rules_pct",
            "p_halve_without_rules_pct")
    return {"ok": True, "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "starter": nav <= 0, "nominal_nav": nominal, "held": held, "best": current,
            "halve_limit_pct": limit, "min_step_pts": min_step, "candidates_tried": tried,
            "on_track_pct": round(float(fs.settings_of(plan)["goal_confidence"]) * 100.0, 1),
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


# --------------------------------------------------------------------------- #
# PR 136b: the card
# --------------------------------------------------------------------------- #
def _name(sid: str) -> str:
    return ((_cat().get(sid) or {}).get("name") or sid)


def _p(x) -> str:
    return f"{float(x):g}%" if x is not None else "?"


async def _lever_sentence(session, user, r: dict) -> str:
    """What would close the gap, in numbers, from the stored goal solve (PR 138).

    The ceiling note used to say only that nothing could be accepted. The levers the
    solve measured -- a target the book can reach, a deposit, a later deadline -- were
    on Today only as separate cards, and a card that was ignored left the owner with no
    sign that the lever existed. Words only: this changes no number and no card."""
    try:
        from app.services.frontier_service import latest
        row = await latest(session, user)
        d = (row.detail or {}) if row is not None else {}
    except Exception:  # noqa: BLE001 - a note must not break Today
        return ""
    lv, g = d.get("levers") or {}, d.get("goal") or {}
    if not lv:
        return ""
    bar = _p(r.get("on_track_pct"))
    parts = []
    from app.services.frontier_service import below_benchmark, benchmark_line
    bm = d.get("benchmark")
    # C26 (owner, 2026-10-23): with beat-the-market sleeves the bar is the index; a
    # target below it is not a lever. The note says how the book compares instead.
    bench_txt = benchmark_line(bm, lv, star=float(r.get("on_track_pct") or 70.0),
                               sized_words=" with your protective rules armed")
    if not below_benchmark(bm, lv) and lv.get("roi_at_confidence_pct") is not None and g.get("roi_pct") is not None \
            and float(lv["roi_at_confidence_pct"]) < float(g["roi_pct"]) and lv.get("amount_at_confidence"):
        parts.append(f"lower your target return from {_p(g['roi_pct'])}/yr to {_p(lv['roi_at_confidence_pct'])}/yr "
                     f"(about {float(lv['amount_at_confidence']):,.0f} by {str(g.get('deadline') or '')[:4]}, "
                     f"a {bar} chance)")
    need = ((d.get("tail") or {}).get("before") or {}).get("extra_monthly_needed")
    if lv.get("extra_monthly_ils"):
        parts.append(f"add about {float(lv['extra_monthly_ils']):,.0f} a month")
    elif lv.get("deadline_extra_years"):
        parts.append(f"move the deadline out by about {float(lv['deadline_extra_years']):g} years")
    if not (lv.get("extra_monthly_ils") or lv.get("deadline_extra_years")):
        tail = ("With your protective rules armed the model finds no deposit and no later deadline that reaches "
                "the bar" + (f"; on the plain history basis, which ignores the rules, about {float(need):,.0f} a month "
                             "more would" if need else "") + ".")
    else:
        tail = ""
    out = f" {bench_txt}" if bench_txt else ""
    if parts:
        out += " What would close the gap: " + "; or ".join(parts) + "."
    if tail:
        out += " " + tail
    return out


async def card(session, user) -> list[dict]:
    """"The best book we can measure for your goal", from the stored result.

    * a better book, by `material_odds_pts` or more -> an actionable card; Accept sets the
      sleeves (sells nothing; the trim and "Fund this sleeve" cards carry the trades);
    * an empty book -> the same card as the STARTER book;
    * nothing material, while the goal chance is under the on-track bar -> one plain card
      that says so, with the best measured and the price of the halving limit (the chance
      without the protective rules, and its chance of halving) -- the one lever left.
    Nothing when the result is about a different book than the one held."""
    from app.services import sleeve_service as sv
    from app.services.frontier_service import _material
    from app.services.recommendations import _rid
    from app.services.sleeve_moves import _sizes_line

    r = await load(session, user)
    if not r.get("ok"):
        return []
    held = {s.strategy_id: float(s.sleeve_pct or 0.0) for s in await sv.list_sleeves(session, user)}
    if {k: round(v, 4) for k, v in held.items()} != {k: round(float(v), 4) for k, v in (r.get("held") or {}).items()}:
        return []
    b, a = r.get("before") or {}, r.get("after") or {}
    if b.get("p_goal_pct") is None:
        return []
    gain = (float(a["p_goal_pct"]) - float(b["p_goal_pct"])) if a.get("p_goal_pct") is not None else 0.0
    steps = r.get("steps") or []
    step_txt = "; ".join(f"{'add' if st['kind'] == 'add' else 'remove'} {_name(st['sleeve'])} (+{st['goal_pts']:g})"
                         for st in steps)
    price = ""
    if b.get("p_goal_without_rules_pct") is not None and b.get("p_halve_without_rules_pct") is not None:
        price = (f" Without your protective rules the chance is {_p(b['p_goal_without_rules_pct'])}, with a "
                 f"{_p(b['p_halve_without_rules_pct'])} chance of the book halving; your halving limit is "
                 f"{_p(r.get('halve_limit_pct'))}. Changing that limit is the one lever left, and it is yours.")
    extras = []
    pr = r.get("protection") or {}
    if pr.get("material"):
        extras.append(f"On that book, retuning your protection measures {_p(pr.get('best_p_goal_pct'))} "
                      f"(halving {_p(pr.get('best_p_halve_pct'))}) -- its own card follows the next solve.")
    co = r.get("core") or {}
    from app.services.frontier_service import CORE_SWITCH
    if co.get("outcome") == CORE_SWITCH and co.get("best_name"):
        extras.append(f"A different core, {co['best_name']}, measures better on that book -- its own card "
                      f"follows the next solve.")
    starter = bool(r.get("starter"))
    if steps and (starter or gain >= _material()):
        return [{
            "id": _rid("optimizer", *sorted(f"{k}:{v:g}" for k, v in (r.get("best") or {}).items())),
            "dimension": "goal", "severity": "HIGH",
            "title": ((f"Your starter book: goal chance {_p(a['p_goal_pct'])}" if starter else
                       f"The best book we can measure for your goal: {_p(b['p_goal_pct'])} -> "
                       f"{_p(a['p_goal_pct'])}")),
            "why": (f"Measured by your goal model with your rules ({str(r.get('as_of', ''))[:10]}): goal chance "
                    f"{_p(b['p_goal_pct'])} -> {_p(a['p_goal_pct'])}; chance the book halves "
                    f"{_p(b.get('p_halve_pct'))} -> {_p(a.get('p_halve_pct'))} (your limit {_p(r.get('halve_limit_pct'))}). "
                    f"Steps: {step_txt}." + (" " + " ".join(extras) if extras else "")),
            "action": f"Accept changes your plan: {_sizes_line(held, r['best'])}.",
            "impact": ("Nothing is bought or sold by Accepting. A sleeve that shrinks gets its trim card and a new "
                       "one gets \"Fund this sleeve\", each naming every trade and its tax first."),
            "how": ["Accept changes the sleeve sizes on your plan",
                    "The trades come next, on their own cards, each shown before it happens",
                    "No brokerage order is placed"],
            "persistent": True,
            "apply": {"kind": "sleeve_move", "held": held, "book": r["best"]},
            "meta": {"optimizer": True, "starter": starter, "before": b, "after": a, "gain_pts": round(gain, 2),
                     "steps": steps, "as_of": r.get("as_of")},
        }]
    if float(b["p_goal_pct"]) < float(r.get("on_track_pct") or 70.0):
        best = a.get("p_goal_pct") if a.get("p_goal_pct") is not None else b["p_goal_pct"]
        levers_txt = await _lever_sentence(session, user, r)
        return [{
            "id": _rid("optimizer_ceiling", round(float(best))),
            "dimension": "goal", "severity": "LOW",
            "title": f"No sleeve change we can measure lifts your goal chance much above {_p(best)}",
            "why": (f"The optimiser tried {r.get('candidates_tried', 0)} changes -- adding or removing strategies "
                    f"from the catalogue, sized by your goal model, with your rules as armed -- and none raises the "
                    f"chance by {_material():g} points or more within your halving limit. Your goal chance "
                    f"is {_p(b['p_goal_pct'])}; your on-track bar is {_p(r.get('on_track_pct'))}." + price + levers_txt),
            "action": "Nothing to accept here. The target, the deadline, the deposits and the halving limit are yours to set on the Plan tab.",
            "impact": "This is what the measurements say, not a recommendation to trade.",
            "how": ["Plan tab -> your goal and its limits", "Holdings -> Rules for the protective rules"],
            "meta": {"optimizer": True, "ceiling": True, "before": b, "after": a, "as_of": r.get("as_of")},
        }]
    return []
