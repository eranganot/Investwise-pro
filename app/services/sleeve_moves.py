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


def candidate_moves(held: dict[str, float], corr: dict | None,
                    overweight: dict[str, float] | None = None) -> list[dict]:
    """Every move to score, from the held sleeves {id: pct}. Pure.

    ``overweight`` (PR 135c): {sleeve id: % of the book it actually holds} for the
    sleeves held above their size by more than the drift band; each gets a "raise"
    move -- the plan's size set to what is held, nothing else changed."""
    from app.services import sleeve_diversity as div
    cat = _cat()
    moves: list[dict] = []
    for s, held_pct in (overweight or {}).items():
        if s in held and held_pct > held[s]:
            book = dict(held)
            book[s] = round(float(held_pct), 1)
            moves.append({"kind": "raise", "from": s, "to": s, "pct": book[s], "book": book})
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
    # PR 135c: sleeves held above their size by more than the drift band get a
    # "raise the size to what you hold" move, for the overweight card's choice.
    from app.services.recommendations import SLEEVE_DRIFT_BAND_PCT, _setting
    band = float(_setting("sleeve_drift_band_pts", SLEEVE_DRIFT_BAND_PCT))
    occ = await sv.sleeve_occupancy(session, user)
    over = {o["strategy_id"]: float(o["held_pct"]) * 100.0 for o in occ.get("sleeves") or []
            if float(o["held_pct"]) * 100.0 - float(o["target_pct"]) > band}
    moves = candidate_moves(held, corr, over)
    # ONE WINDOW PER STRATEGY (PR 135c). Loading every candidate into one solve
    # let the youngest fund (KMLM, 2020) shorten the price window for all of them,
    # which moved the book's own score (production, 2026-10-18: 8.5% -> 7.7%).
    # Each strategy a move adds is scored in its own group, beside the book as it
    # is ON THAT GROUP'S WINDOW, so every delta compares like with like. Moves
    # that add nothing (remove, raise) share the plain group, whose baseline is
    # the book's headline -- the same solve the goal sheet's preview runs.
    _keys = ("p_goal_pct", "p_halve_pct", "median", "growth_cagr_pct")
    groups: dict[str | None, list[dict]] = {}
    for mv in moves:
        add = mv.get("to") if (mv.get("to") and mv["to"] not in held) else None
        groups.setdefault(add, []).append(mv)
    groups.setdefault(None, [])
    headline = None
    out = []
    for add, ms in groups.items():
        res = await fs.score_books(session, user, [held] + [mv["book"] for mv in ms],
                                   extra_sleeves=[add] if add else None)
        if not res.get("ok"):
            if add is None:
                return {"ok": False, "reason": res.get("reason")}
            out += [{**{k: v for k, v in mv.items()}, "ok": False, "reason": res.get("reason")} for mv in ms]
            continue
        base, scores = res["scores"][0], res["scores"][1:]
        if add is None:
            if not base.get("ok"):
                return {"ok": False, "reason": base.get("reason"), "detail": base.get("detail")}
            headline = {k: base.get(k) for k in _keys}
        for mv, sc in zip(ms, scores):
            row = dict(mv)
            row["ok"] = bool(sc.get("ok") and base.get("ok"))
            if row["ok"]:
                row["before"] = {k: base.get(k) for k in _keys}
                row["after"] = {k: sc.get(k) for k in _keys}
                row["goal_pts"] = _delta(base, sc, "p_goal_pct")
                row["halve_pts"] = _delta(base, sc, "p_halve_pct")
            else:
                row["reason"] = sc.get("reason") or base.get("reason")
            out.append(row)
    return {"ok": True, "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "held": held, "before": headline, "moves": out}


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


# --------------------------------------------------------------------------- #
# PR 135b: the cards, and what Accept does
# --------------------------------------------------------------------------- #
#: Card budget per kind, so Today shows the best moves rather than all forty.
MAX_RAISES = 2
MAX_SAFER = 1


def _name(sid: str | None) -> str:
    return ((_cat().get(sid) or {}).get("name") or sid or "")


def _pct(x) -> str:
    return f"{float(x):g}%" if x is not None else "?"


def _sizes_line(held: dict, book: dict) -> str:
    """Every sleeve whose size changes, before -> after."""
    parts = []
    for sid in sorted(set(held) | set(book)):
        a, b = float(held.get(sid, 0.0)), float(book.get(sid, 0.0))
        if abs(a - b) > 1e-6:
            parts.append(f"{_name(sid)} {a:g}% -> {b:g}%")
    return "; ".join(parts)


def _title(m: dict) -> str:
    if m["kind"] == "hedge":
        return f"Move {m['pct']:g}% of {_name(m['from'])} into {_name(m['to'])}"
    if m["kind"] == "remove":
        return f"Remove the {_name(m['from'])} sleeve"
    return f"Replace {_name(m['from'])} with {_name(m['to'])}"


def select(stored: dict, material_pts: float) -> list[dict]:
    """The moves worth a card. Pure.

    * every REPLACE of a same-bet pair -- the best replacement for each sleeve that could go;
    * up to MAX_RAISES moves that raise the goal's chance by `material_pts` or more;
    * up to MAX_SAFER "safer, lower chance" moves (owner decision 4): the chance of halving falls by
      `material_pts` or more while the goal's chance does not rise by that much.
    """
    ok = [m for m in (stored.get("moves") or []) if m.get("ok") and m.get("goal_pts") is not None
          and m["kind"] != "raise"]          # a raise is a choice on the overweight card, not a card
    out: list[dict] = []
    best_replace: dict[str, dict] = {}
    for m in ok:
        if m["kind"] == "replace":
            cur = best_replace.get(m["from"])
            if cur is None or m["after"]["p_goal_pct"] > cur["after"]["p_goal_pct"]:
                best_replace[m["from"]] = m
    out += [{**m, "verdict": "same_bet"} for m in best_replace.values()]
    rest = [m for m in ok if m["kind"] != "replace"]
    raises = sorted((m for m in rest if m["goal_pts"] >= material_pts), key=lambda m: -m["goal_pts"])
    out += [{**m, "verdict": "raises"} for m in raises[:MAX_RAISES]]
    shown = {id(m) for m in raises[:MAX_RAISES]}
    safer = sorted((m for m in rest if id(m) not in shown and m.get("halve_pts") is not None
                    and m["halve_pts"] <= -material_pts and m["goal_pts"] < material_pts),
                   key=lambda m: (m["halve_pts"], -m["goal_pts"]))
    out += [{**m, "verdict": "safer"} for m in safer[:MAX_SAFER]]
    return out


async def cards(session, user) -> list[dict]:
    """Today's sleeve cards, from the stored scores. Nothing when the scores are
    about a different book (a sleeve changed since): a card about sizes you no
    longer hold would claim a move it cannot make."""
    from app.services import sleeve_service as sv
    from app.services.frontier_service import _material
    from app.services.recommendations import _rid

    stored = await load(session, user)
    if not stored.get("ok"):
        return []
    held = {s.strategy_id: float(s.sleeve_pct or 0.0) for s in await sv.list_sleeves(session, user)}
    if {k: round(v, 4) for k, v in held.items()} != {k: round(float(v), 4) for k, v in stored["held"].items()}:
        return []
    out = []
    for m in select(stored, _material()):
        # The move's own baseline (its window), so before -> after never mixes two.
        b = m.get("before") or stored["before"]
        a = m["after"]
        goal_txt = f"goal chance {_pct(b['p_goal_pct'])} -> {_pct(a['p_goal_pct'])}"
        halve_txt = (f"chance the book halves {_pct(b['p_halve_pct'])} -> {_pct(a['p_halve_pct'])}"
                     if b.get("p_halve_pct") is not None and a.get("p_halve_pct") is not None else "")
        if m["verdict"] == "raises":
            lead = "Raises your chance of reaching the goal."
        elif m["verdict"] == "safer":
            lead = (f"Safer, lower chance: it costs {abs(m['goal_pts']):g} points of goal chance for "
                    f"{abs(m['halve_pts']):g} points less chance of the book halving." if m["goal_pts"] < 0 else
                    f"Safer: {abs(m['halve_pts']):g} points less chance of the book halving.")
        else:
            lead = (f"{_name(m['from'])} and {_name(m['keep'])} are the same bet (D97), so they count as one. "
                    f"Of the strategies that are different from what you keep, {_name(m['to'])} does the most "
                    f"for your goal.")
        out.append({
            "id": _rid("sleeve_move", m["kind"], m.get("from"), m.get("to"), m.get("scale")),
            "dimension": "strategy",
            "severity": "HIGH" if m["verdict"] == "same_bet" else "MEDIUM",
            "title": (("Safer, lower chance: " if m["verdict"] == "safer" else "") + _title(m)),
            "why": (f"{lead} Measured by your goal model ({stored.get('as_of', '')[:10]}): "
                    f"{goal_txt}" + (f"; {halve_txt}" if halve_txt else "") + "."),
            "action": f"Accept changes your plan: {_sizes_line(held, m['book'])}.",
            "impact": ("Nothing is bought or sold by Accepting. The sleeve that shrinks gets its trim card and a "
                       "new sleeve gets \"Fund this sleeve\", each naming every trade and its tax first."),
            "how": ["Accept changes the sleeve sizes on your plan",
                    "The trades come next, on their own cards, each shown before it happens",
                    "No brokerage order is placed"],
            "persistent": True,
            "apply": {"kind": "sleeve_move", "held": held, "book": m["book"]},
            "meta": {"move": m["kind"], "verdict": m["verdict"], "from": m.get("from"), "to": m.get("to"),
                     "scale": m.get("scale"), "before": b, "after": a,
                     "goal_pts": m["goal_pts"], "halve_pts": m.get("halve_pts"), "as_of": stored.get("as_of")},
        })
    return out


def raise_recommendation(move: dict | None, material_pts: float) -> tuple[str, str]:
    """PR 135c: "trim" or "raise" for an overweight sleeve, and why. Pure.

    The rule, fixed before any card showed it: recommend RAISE only when raising the
    sleeve to what is held does not lower the goal's chance and does not raise the
    chance of the book halving by `material_pts` or more. Otherwise TRIM -- also when
    the raise could not be measured, which the reason says."""
    if not move or not move.get("ok") or move.get("goal_pts") is None:
        return "trim", "Raising could not be measured, so the size you chose stands."
    g, h = float(move["goal_pts"]), move.get("halve_pts")
    if g >= 0 and (h is None or float(h) < material_pts):
        return "raise", (f"Measured by your goal model: keeping it raises the goal chance by {g:g} points"
                         + (f" and changes the chance of halving by {float(h):+g}." if h is not None else "."))
    if g < 0:
        return "trim", f"Measured by your goal model: keeping it would cost {abs(g):g} points of goal chance."
    return "trim", (f"Measured by your goal model: keeping it adds {float(h):g} points to the chance of the "
                    f"book halving.")


async def overweight_choice(session, user, sleeve_ids: list[str]) -> dict | None:
    """PR 135c: the stored "raise" move for the first of `sleeve_ids` that has one,
    with its recommendation -- only while the scores are about the sleeves held."""
    from app.services import sleeve_service as sv
    from app.services.frontier_service import _material

    stored = await load(session, user)
    if not stored.get("ok"):
        return None
    held = {s.strategy_id: float(s.sleeve_pct or 0.0) for s in await sv.list_sleeves(session, user)}
    if {k: round(v, 4) for k, v in held.items()} != {k: round(float(v), 4) for k, v in stored["held"].items()}:
        return None
    for sid in sleeve_ids:
        mv = next((m for m in stored.get("moves") or [] if m["kind"] == "raise" and m["from"] == sid), None)
        if mv:
            rec, why = raise_recommendation(mv, _material())
            return {"sleeve": sid, "move": mv, "held": held, "recommended": rec, "why": why,
                    "before": stored.get("before"), "as_of": stored.get("as_of")}
    return None


async def apply_move(session, user, spec: dict) -> dict:
    """Accept a sleeve card: set every sleeve to the card's book. Refuses untouched
    when the book moved since it was scored. Sells nothing."""
    from app.services import sleeve_service as sv
    from app.services.strategy_service import _arm_sleeve_caps

    held = {s.strategy_id: float(s.sleeve_pct or 0.0) for s in await sv.list_sleeves(session, user)}
    want_held = {k: float(v) for k, v in (spec.get("held") or {}).items()}
    if {k: round(v, 4) for k, v in held.items()} != {k: round(v, 4) for k, v in want_held.items()}:
        return {"ok": False, "refused": "BOOK_CHANGED",
                "error": "your sleeves changed since this move was scored - nothing was changed"}
    book = {k: float(v) for k, v in (spec.get("book") or {}).items()}
    changed = []
    for sid in sorted(held):                       # shrink and remove first, so the room exists
        if sid not in book or book[sid] <= 0:
            r = await sv.remove(session, user, sid)
            if not r.get("ok"):
                await session.rollback()
                return {"ok": False, "refused": "REMOVE_REFUSED", "error": r.get("error")}
            changed.append({"strategy_id": sid, "from_pct": held[sid], "to_pct": 0.0})
        elif book[sid] < held[sid]:
            r = await sv.add_or_resize(session, user, sid, book[sid])
            if not r.get("ok"):
                await session.rollback()
                return {"ok": False, "refused": "RESIZE_REFUSED", "error": r.get("reason") or r.get("error")}
            changed.append({"strategy_id": sid, "from_pct": held[sid], "to_pct": book[sid]})
    for sid in sorted(book):
        if book[sid] > 0 and book[sid] > held.get(sid, 0.0):
            r = await sv.add_or_resize(session, user, sid, book[sid])
            if not r.get("ok"):
                await session.rollback()
                return {"ok": False, "refused": "RESIZE_REFUSED", "error": r.get("reason") or r.get("error")}
            changed.append({"strategy_id": sid, "from_pct": held.get(sid, 0.0), "to_pct": book[sid]})
    await session.commit()
    caps = await _arm_sleeve_caps(session, user)
    await session.commit()
    return {"ok": True, "applied": "plan", "sleeves": changed, "caps": caps,
            "note": ("Your plan is changed. Nothing was bought or sold: the trim and \"Fund this sleeve\" "
                     "cards now show the trades.")}


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
