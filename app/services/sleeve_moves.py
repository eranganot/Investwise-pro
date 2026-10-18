"""PR 135a (wave 4): every sleeve move worth a card, scored by the goal model.

The sleeve plan's wave 4 (owner-approved 2026-10-15): cards that add, remove, replace and resize sleeves, each
showing what it does to the goal's chance (with your rules) and to the chance the book halves -- "both ways"
(owner decision 4): a move that raises the chance, and a "safer, lower chance" one.

Scoring a book is a light goal solve (`frontier_service.score_books`, 10-20 per run), too slow for every Today
load, so this runs nightly (job `sleeve_moves`, after the 04:10 goal solve) and on demand, and Today only reads
what it stored (`kv_settings`, `sleeve_moves:<email>`). Read-only on the book: it stores scores, never trades.

Moves (each a complete book of sleeve sizes; anything dropped returns to the core):
  * hedge   -- move a hedge's suggested size (1x and 2x) out of a leveraged sleeve into a catalogue hedge
              ("Hedge for a leveraged sleeve" badge) that is not the same bet as what remains (D97);
  * remove  -- drop one sleeve;
  * replace -- for a pair of held sleeves that are the same bet: drop one, add a catalogue strategy that is a
              different bet, at the dropped one's size (each way round).

Not financial advice.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("investwise")

KV_PREFIX = "sleeve_moves:"
HEDGE_BADGE = "Hedge for a leveraged sleeve"
#: A sleeve whose catalogue risk is one of these can fund a hedge ("the leveraged sleeve").
LEVERAGED_RISK = {"High", "Very high"}


def _cat():
    from app.services import strategy_catalog
    return strategy_catalog


def hedge_ids() -> list[str]:
    cat = _cat()
    return [i for i in cat.active_ids()
            if HEDGE_BADGE in ((cat.get(i) or {}).get("badges") or []) and not (cat.get(i) or {}).get("explore_only")]


def candidate_moves(held: dict[str, float], corr: dict | None) -> list[dict]:
    """Every move to score, from the held sleeves {id: pct}. Pure."""
    from app.services import sleeve_diversity as div
    cat = _cat()
    moves: list[dict] = []
    levered = [s for s in held if (cat.get(s) or {}).get("risk") in LEVERAGED_RISK]
    for src in levered:
        for h in hedge_ids():
            if h in held:
                continue
            if div.same_bets_for(h, list(held), corr):
                continue                                   # it would join a look-alike
            base = float((cat.get(h) or {}).get("sleeve_pct") or 0)
            for scale in (1, 2):
                size = min(base * scale, held[src])
                if size <= 0:
                    continue
                book = dict(held)
                book[src] = round(held[src] - size, 4)
                book[h] = size
                moves.append({"kind": "hedge", "from": src, "to": h, "pct": size, "scale": scale, "book": book})
    for s in held:
        book = {k: v for k, v in held.items() if k != s}
        moves.append({"kind": "remove", "from": s, "to": None, "pct": held[s], "book": book})
    ids = list(held)
    for n, a in enumerate(ids):
        for b in ids[n + 1:]:
            if not div.same_bet(a, b, corr):
                continue
            for drop, keep in ((a, b), (b, a)):
                for c in cat.active_ids():
                    if c in held or (cat.get(c) or {}).get("explore_only"):
                        continue
                    others = [x for x in held if x != drop]
                    if div.same_bets_for(c, others, corr):
                        continue
                    book = {k: v for k, v in held.items() if k != drop}
                    book[c] = held[drop]
                    moves.append({"kind": "replace", "from": drop, "keep": keep, "to": c, "pct": held[drop],
                                  "book": book})
    return moves


def _delta(before: dict, after: dict, key: str) -> float | None:
    if before.get(key) is None or after.get(key) is None:
        return None
    return round(float(after[key]) - float(before[key]), 2)


async def compute(session, user) -> dict:
    """Score the book as it is and every candidate move. Read-only."""
    from app.services import frontier_service as fs
    from app.services import sleeve_diversity as div
    from app.services import sleeve_service as sv

    held = {s.strategy_id: float(s.sleeve_pct or 0.0) for s in await sv.list_sleeves(session, user)}
    if not held:
        return {"ok": False, "reason": "NO_SLEEVES"}
    corr = await div.load_correlations(session)
    moves = candidate_moves(held, corr)
    extra = sorted({m["to"] for m in moves if m.get("to")} - set(held))
    res = await fs.score_books(session, user, [held] + [m["book"] for m in moves], extra_sleeves=extra)
    if not res.get("ok"):
        return {"ok": False, "reason": res.get("reason")}
    base, scores = res["scores"][0], res["scores"][1:]
    if not base.get("ok"):
        return {"ok": False, "reason": base.get("reason"), "detail": base.get("detail")}
    out = []
    for m, sc in zip(moves, scores):
        row = {k: v for k, v in m.items() if k != "book"}
        row["book"] = m["book"]
        row["ok"] = bool(sc.get("ok"))
        if sc.get("ok"):
            row["after"] = {k: sc.get(k) for k in ("p_goal_pct", "p_halve_pct", "median", "growth_cagr_pct")}
            row["goal_pts"] = _delta(base, sc, "p_goal_pct")
            row["halve_pts"] = _delta(base, sc, "p_halve_pct")
        else:
            row["reason"] = sc.get("reason")
        out.append(row)
    return {"ok": True, "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "held": held, "before": {k: base.get(k) for k in ("p_goal_pct", "p_halve_pct", "median",
                                                              "growth_cagr_pct")},
            "moves": out}


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
    """Compute and store. A failed run keeps what was stored before."""
    from app.models.tables import KVSetting
    out = await compute(session, user)
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
    """Nightly: one run per book that holds sleeves. One bad book never stops the rest."""
    from sqlalchemy import distinct, select

    from app.models.tables import PlanSleeve, User
    subjects = list((await session.scalars(select(distinct(PlanSleeve.subject)))).all())
    done = failed = 0
    for subject in subjects:
        user = (await session.scalars(select(User).where(User.email == subject))).first()
        if user is None:
            continue
        try:
            r = await refresh(session, user)
            done += 1 if r.get("stored") else 0
            failed += 0 if r.get("stored") else 1
        except Exception:  # noqa: BLE001
            logger.warning("sleeve moves failed for a subject", exc_info=False)
            failed += 1
    return {"sleeve_moves_stored": done, "sleeve_moves_failed": failed}


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


def run_sleeve_moves_blocking() -> dict:
    """APScheduler entrypoint. RAISES when every book failed, so `_guarded` records it."""
    import asyncio
    res = asyncio.run(_job())
    if res["sleeve_moves_failed"] and not res["sleeve_moves_stored"]:
        raise RuntimeError(f"sleeve moves: every book failed ({res['sleeve_moves_failed']})")
    logger.info("sleeve moves: %s", res)
    return res
