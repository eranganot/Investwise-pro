"""A3r (D132): one exit per holding -- the protection manager.

Owner, 2026-10-24, on the rules review (39 active rules; every core fund carried a fixed
stop ~8% down, a 10-17% trailing stop and a take-profit, armed by five generators that
never asked each other):

* **Core funds** (everything held that no sleeve holds and that is not a 2x/3x fund):
  no per-fund exit at all -- no stop-loss, trailing stop, take-profit or profit-protect.
  The rebalance bands and the book's drawdown guard protect them ("Bands + book guard only").
* **Sleeve and tactical holdings** (a sleeve's tickers, and any 2x/3x fund held):
  exactly ONE exit, a trailing stop at k times the holding's measured monthly volatility,
  k picked on the goal replay from `K_OPTIONS` ("From measured volatility").
* **Take-profits go** everywhere ("Drop them"): the bands already trim what outgrows its target.

The manager is the one place that proposes a protective exit: the card generators no longer
do (`filter_generated`). Nightly (job `protection_manager`, 05:40 UTC, after the goal solve)
and on demand it measures the change on the goal replay -- today's rules against each k --
and stores the plan per user in `kv_settings`; Today's card reads it and offers the whole
change as one Accept. A retired exit is DELETED, not paused, so the 3-day auto re-arm (C18)
cannot bring it back. The rebalance bands, buy-backs, alerts, strategy signals and the book
guard are never touched. Rules on tickers no longer held are left to the purge.

Not financial advice.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

logger = logging.getLogger("investwise")

KV_PREFIX = "protection_manager:"
EXIT_TYPES = ("stop_loss", "trailing_stop", "take_profit", "profit_protect")
K_OPTIONS = (2.0, 3.0, 4.0)
MIN_TRAIL_PCT, MAX_TRAIL_PCT = 5.0, 60.0
EDIT_TOLERANCE_PTS = 0.5
CARD_KIND = "protection_plan"
CARD_ID = "rec_protection_one_exit"


# --------------------------------------------------------------------------- #
# pure parts
# --------------------------------------------------------------------------- #
def monthly_vol_pct(closes: list[float], window: int = 250) -> float | None:
    """Std-dev of daily log returns over the last `window` sessions, scaled to a month (21 sessions), in %."""
    tail = [float(c) for c in closes[-(window + 1):] if c and c > 0]
    rets = [math.log(tail[i] / tail[i - 1]) for i in range(1, len(tail))]
    if len(rets) < 60:
        return None
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(21) * 100.0, 2)


def trail_for(vol_pct: float, k: float) -> float:
    return float(min(MAX_TRAIL_PCT, max(MIN_TRAIL_PCT, round(k * vol_pct, 1))))


def role_of(ticker: str, sleeve_tickers: set[str], geared: bool) -> str:
    tk = ticker.upper()
    return "sleeve" if tk in sleeve_tickers or geared else "core"


def plan_changes(rules: list[dict], roles: dict[str, str], vols: dict[str, float | None], k: float) -> dict:
    """The changes that leave each held ticker with the exits its role allows. Pure.

    `rules`: every exit rule of the user ({id, ticker, rule_type, mode, level, active}),
    armed or not (a paused one would come back with the 3-day re-arm). Returns
    {delete: [...], edit: [...], create: [...], unchanged: [tickers], no_vol: [tickers]}.
    """
    by: dict[str, list[dict]] = {}
    for r in rules:
        if r["rule_type"] in EXIT_TYPES:
            by.setdefault(str(r["ticker"]).upper(), []).append(r)
    out = {"delete": [], "edit": [], "create": [], "unchanged": [], "no_vol": []}
    for tk, role in sorted(roles.items()):
        exits = by.get(tk, [])
        if role == "core":
            out["delete"] += [_ref(r, "core fund: the bands and the book guard protect it") for r in exits]
            if not exits:
                out["unchanged"].append(tk)
            continue
        vol = vols.get(tk)
        if vol is None:
            out["no_vol"].append(tk)          # nothing measured: leave this holding as it is
            continue
        want = trail_for(vol, k)
        trails = sorted((r for r in exits if r["rule_type"] == "trailing_stop"),
                        key=lambda r: (not r.get("active"), -float(r.get("level") or 0)))
        keep = trails[0] if trails else None
        for r in exits:
            if r is not keep:
                out["delete"].append(_ref(r, "one exit per holding: its trailing stop is the one"))
        if keep is None:
            out["create"].append({"ticker": tk, "rule_type": "trailing_stop", "mode": "pct", "level": want,
                                  "vol_pct": vol, "k": k})
        elif abs(float(keep.get("level") or 0) - want) > EDIT_TOLERANCE_PTS or not keep.get("active"):
            out["edit"].append({**_ref(keep, ""), "to_level": want, "vol_pct": vol, "k": k})
        elif not [r for r in exits if r is not keep]:
            out["unchanged"].append(tk)
    return out


def _ref(r: dict, why: str) -> dict:
    return {"rule_id": str(r["id"]), "ticker": str(r["ticker"]).upper(), "rule_type": r["rule_type"],
            "mode": r.get("mode"), "level": float(r.get("level") or 0), "from_level": float(r.get("level") or 0),
            "active": bool(r.get("active")), "why": why}


def is_empty(ch: dict) -> bool:
    return not (ch.get("delete") or ch.get("edit") or ch.get("create"))


def preview_args(ch: dict) -> tuple[dict, list[dict]]:
    """The change as the goal preview reads it: retired and edited rules switched off,
    the new and edited trailing stops proposed."""
    armed = {d["rule_id"]: False for d in ch["delete"]}
    armed.update({e["rule_id"]: False for e in ch["edit"]})
    extra = [{"ticker": c["ticker"], "rule_type": "trailing_stop", "mode": "pct", "level": c["level"]}
             for c in ch["create"]]
    extra += [{"ticker": e["ticker"], "rule_type": "trailing_stop", "mode": "pct", "level": e["to_level"]}
              for e in ch["edit"]]
    return armed, extra


def fingerprint(rules: list[dict]) -> list:
    return sorted([str(r["id"]), r["rule_type"], round(float(r.get("level") or 0), 4), bool(r.get("active"))]
                  for r in rules if r["rule_type"] in EXIT_TYPES)


def filter_generated(recs: list[dict]) -> tuple[list[dict], list[str]]:
    """Generated cards may not arm an exit: the manager is the one place that does (D132).
    A card that would arm one is dropped whole -- its words are about that exit. Returns
    (kept, dropped ids)."""
    kept, dropped = [], []
    for c in recs:
        ap = c.get("apply") or {}
        arms = ([r.get("rule_type") for r in ap.get("rules") or []] if ap.get("kind") == "create_rules"
                else [ap.get("rule_type")] if ap.get("kind") == "create_rule" else [])
        if any(t in EXIT_TYPES for t in arms):
            dropped.append(c.get("id"))
            continue
        kept.append(c)
    return kept, dropped


def filter_suggestions(sugg: list[dict]) -> list[dict]:
    """The same for the Rules page's suggestions."""
    out = []
    for h in sugg or []:
        rules = [r for r in h.get("rules") or [] if r.get("rule_type") not in EXIT_TYPES]
        if rules:
            out.append({**h, "rules": rules})
    return out


# --------------------------------------------------------------------------- #
# measuring
# --------------------------------------------------------------------------- #
def _vols_blocking(tks: list[str]) -> dict[str, float | None]:
    """Each holding's monthly volatility from its own history. Blocking: run through `offload`."""
    from app.providers.registry import guarded_history
    out = {}
    for tk in tks:
        try:
            hist = guarded_history(tk, days=400)
            out[tk] = monthly_vol_pct([c for _d, c in hist] if hist else [])
        except Exception:  # noqa: BLE001 -- no history: this holding is left as it is
            out[tk] = None
    return out


async def _inputs(session, user) -> dict:
    from sqlalchemy import select

    from app.core.offload import offload
    from app.models.tables import TradingRule
    from app.services import leveraged_ceiling as lc
    from app.services import sleeve_service as sv
    from app.services.intake_service import is_cash_position, list_positions

    positions = await list_positions(session, user)
    held = sorted({(p.ticker or "").upper() for p in positions
                   if float(p.quantity or 0) > 0
                   and not is_cash_position(p.ticker, p.meta if isinstance(p.meta, dict) else None)})
    sleeve_tk = {t.upper() for t in await sv.sleeve_tickers(session, user)}
    roles = {tk: role_of(tk, sleeve_tk, lc.is_geared(tk)) for tk in held}
    rows = (await session.scalars(select(TradingRule).where(TradingRule.subject == user.email))).all()
    rules = [{"id": str(r.id), "ticker": r.ticker, "rule_type": r.rule_type, "mode": r.mode,
              "level": float(r.level or 0), "active": bool(r.active)} for r in rows]
    rules = [r for r in rules if str(r["ticker"]).upper() in roles]

    vols = await offload(_vols_blocking, [tk for tk, r in roles.items() if r == "sleeve"])
    return {"roles": roles, "rules": rules, "vols": vols}


async def measure(session, user, *, inputs: dict | None = None) -> dict:
    """Today's exits against the managed set at each k, on the goal replay. Read-only."""
    from app.services import frontier_service as fs
    from app.services.plan_service import get_plan

    plan = await get_plan(session, user)
    if plan is None or getattr(plan, "target_roi_pct", None) is None:
        return {"ok": False, "reason": "NO_GOAL"}
    inp = inputs or await _inputs(session, user)
    limit = float(fs.settings_of(plan)["halve_limit_pct"])
    base = {"ok": True, "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "roles": inp["roles"], "vols": inp["vols"], "halve_limit_pct": limit,
            "fingerprint": fingerprint(inp["rules"]), "k_options": list(K_OPTIONS)}
    changes = {k: plan_changes(inp["rules"], inp["roles"], inp["vols"], k) for k in K_OPTIONS}
    if all(is_empty(c) for c in changes.values()):
        return {**base, "changes": None, "current": None, "tried": []}
    cur = await fs.preview(session, user)
    if not cur.get("ok"):
        return {**base, "ok": False, "reason": cur.get("reason")}
    keys = ("amount_at_confidence", "roi_at_confidence_pct", "p_halve_pct", "median", "worst5_mean")
    cur_s = {k2: cur.get(k2) for k2 in keys} | {"p_goal_pct": (cur.get("headline") or {}).get("p_goal_pct")}
    tried = []
    for k, ch in changes.items():
        armed, extra = preview_args(ch)
        v = await fs.preview(session, user, armed=armed, extra_rules=extra)
        if not v.get("ok"):
            continue
        st = {k2: v.get(k2) for k2 in keys} | {"p_goal_pct": (v.get("headline") or {}).get("p_goal_pct")}
        tried.append({"k": k, "stats": st, "changes": ch})
    if not tried:
        return {**base, "ok": False, "reason": "NOT_MEASURABLE"}

    def ok_halve(st):
        h, h0 = st.get("p_halve_pct"), cur_s.get("p_halve_pct")
        return h is not None and (h <= limit or (h0 is not None and h <= h0))

    inside = [t for t in tried if ok_halve(t["stats"])]
    pool = inside or tried
    best = max(pool, key=lambda t: (float(t["stats"].get("amount_at_confidence") or 0.0),
                                    -float(t["stats"].get("p_halve_pct") or 0.0))) if inside else \
        min(pool, key=lambda t: float(t["stats"].get("p_halve_pct") or 1e9))
    return {**base, "current": cur_s, "tried": [{"k": t["k"], "stats": t["stats"]} for t in tried],
            "k": best["k"], "best": best["stats"], "changes": best["changes"], "within_halving": bool(inside)}


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
    out = await measure(session, user)
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
            logger.warning("protection manager failed for a user", exc_info=False)
            failed += 1
    return {"protection_stored": done, "protection_failed": failed}


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


def run_protection_manager_blocking() -> dict:
    """APScheduler entrypoint. RAISES when every user failed, so `_guarded` records it."""
    import asyncio
    res = asyncio.run(_job())
    if res["protection_failed"] and not res["protection_stored"]:
        raise RuntimeError(f"protection manager: every user failed ({res['protection_failed']})")
    logger.info("protection manager: %s", res)
    return res


# --------------------------------------------------------------------------- #
# the card
# --------------------------------------------------------------------------- #
_WORDS = {"stop_loss": "stop-loss", "trailing_stop": "trailing stop", "take_profit": "take-profit",
          "profit_protect": "profit-protect"}


def _lvl(d: dict, level: float) -> str:
    return f"{level:g}%" if d.get("mode") == "pct" else f"{level:,.2f}"


def _ils(x) -> str:
    return f"₪{float(x):,.0f}" if x is not None else "?"


def _pct(x) -> str:
    return f"{float(x):g}%" if x is not None else "?"


async def card(session, user) -> list[dict]:
    """One card: every change, measured, one Accept. Nothing when the stored plan is about
    another rule set (anything changed since it was measured) or there is nothing to change."""
    r = await load(session, user)
    if not r.get("ok") or not r.get("changes") or is_empty(r["changes"]):
        return []
    inp = await _inputs_rules_only(session, user, r.get("roles") or {})
    if fingerprint(inp) != r.get("fingerprint"):
        return []
    ch, cur, best = r["changes"], r.get("current") or {}, r.get("best") or {}
    n_del, n_set = len(ch["delete"]), len(ch["edit"]) + len(ch["create"])
    core = sorted(tk for tk, ro in (r.get("roles") or {}).items() if ro == "core")
    how = []
    for d in ch["delete"]:
        how.append(f"Remove: {d['ticker']} {_WORDS.get(d['rule_type'], d['rule_type'])} {_lvl(d, d['level'])}"
                   + ("" if d.get("active") else " (switched off)"))
    for e in ch["edit"]:
        how.append(f"Set: {e['ticker']} trailing stop {e['from_level']:g}% -> {e['to_level']:g}% "
                   f"({e['k']:g}x its monthly volatility, {e['vol_pct']:g}%)")
    for c in ch["create"]:
        how.append(f"Add: {c['ticker']} trailing stop {c['level']:g}% ({c['k']:g}x its monthly volatility, "
                   f"{c['vol_pct']:g}%)")
    tried = "; ".join(f"{t['k']:g}x: {_ils(t['stats'].get('amount_at_confidence'))}, halving "
                      f"{_pct(t['stats'].get('p_halve_pct'))}" for t in r.get("tried") or [])
    when = str(r.get("as_of", ""))[:10]
    why = (f"Today {sum(1 for _ in r.get('fingerprint') or [])} exit rules sit on your holdings, several on one fund, "
           f"armed by different parts of the app. Your choice (2026-10-24): core funds "
           f"({', '.join(core) or 'none'}) have no exit of their own -- the rebalance bands and the book's drawdown "
           f"guard protect them; each sleeve or 2x/3x holding has one trailing stop sized from its own volatility; "
           f"no take-profits. Measured on your goal model ({when}): at your bar about "
           f"{_ils(cur.get('amount_at_confidence'))} -> {_ils(best.get('amount_at_confidence'))}"
           + (f" ({cur['roi_at_confidence_pct']:.1f}%/yr -> {best['roi_at_confidence_pct']:.1f}%/yr)"
              if cur.get("roi_at_confidence_pct") is not None and best.get("roi_at_confidence_pct") is not None
              else "")
           + f"; chance the book halves {_pct(cur.get('p_halve_pct'))} -> {_pct(best.get('p_halve_pct'))} "
             f"(your limit {_pct(r.get('halve_limit_pct'))}). Stop widths tried: {tried}.")
    if not r.get("within_halving"):
        why += " No width keeps the halving chance inside your limit; this is the one closest to it."
    if (ch.get("no_vol") or []):
        why += f" Left as they are (no price history to size a stop): {', '.join(ch['no_vol'])}."
    return [{
        "id": CARD_ID, "dimension": "discipline", "severity": "HIGH",
        "title": f"One exit per holding: remove {n_del} rule{'s' if n_del != 1 else ''}, "
                 f"set {n_set} trailing stop{'s' if n_set != 1 else ''}",
        "why": why,
        "action": "Accept makes every change below in one go. Nothing is bought or sold.",
        "impact": ("Removed rules are deleted (not paused), so the 3-day auto re-arm cannot bring them back. "
                   "Your rebalance bands, buy-backs, alerts, strategy signals and the drawdown guard are untouched."),
        "how": how + [f"Solved {when}", "No brokerage order is placed"],
        "persistent": True, "actionable": True,
        "apply": {"kind": CARD_KIND, "delete": ch["delete"], "edit": ch["edit"], "create": ch["create"],
                  "k": r.get("k")},
        "meta": {"protection_manager": True, "current": cur, "best": best, "tried": r.get("tried"),
                 "as_of": r.get("as_of")},
    }]


async def _inputs_rules_only(session, user, roles: dict) -> list[dict]:
    from sqlalchemy import select

    from app.models.tables import TradingRule
    rows = (await session.scalars(select(TradingRule).where(TradingRule.subject == user.email))).all()
    return [{"id": str(r.id), "ticker": r.ticker, "rule_type": r.rule_type, "level": float(r.level or 0),
             "active": bool(r.active)} for r in rows if str(r.ticker).upper() in roles]


async def apply(session, user, spec: dict) -> dict:
    """Accept: delete, re-level and add exactly what was measured. Refuses whole, before any
    write, when a rule it names is gone or no longer at the measured level (RULES_CHANGED)."""
    import uuid

    from app.models.tables import RuleEvent, TradingRule
    from app.services.rules_service import create_rule

    now = datetime.now(timezone.utc)
    found = {}
    for d in [*(spec.get("delete") or []), *(spec.get("edit") or [])]:
        try:
            r = await session.get(TradingRule, uuid.UUID(str(d["rule_id"])))
        except Exception:  # noqa: BLE001
            r = None
        if r is None or r.subject != user.email or r.rule_type != d["rule_type"] \
                or abs(float(r.level or 0) - float(d["from_level"])) > 1e-6:
            return {"ok": False, "refused": "RULES_CHANGED",
                    "error": "a rule this card would change has moved since it was measured - nothing was changed."}
        found[str(d["rule_id"])] = r

    def event(r_or_d, outcome: str, title: str, action: dict):
        session.add(RuleEvent(subject=user.email, rule_id=str(r_or_d["rule_id"]), ticker=r_or_d["ticker"],
                              rule_type=r_or_d["rule_type"], trigger_price=None, target_price=None,
                              title=title[:200], outcome=outcome, outcome_at=now,
                              action={"by": "card", "card": CARD_ID, **action}))

    removed, set_, added = [], [], []
    for d in spec.get("delete") or []:
        await session.delete(found[str(d["rule_id"])])
        event(d, "deleted", f"Removed by 'One exit per holding': {d['ticker']} {_WORDS.get(d['rule_type'])}",
              {"level": d["from_level"], "why": d.get("why")})
        removed.append({"ticker": d["ticker"], "rule_type": d["rule_type"], "level": d["from_level"]})
    for e in spec.get("edit") or []:
        r = found[str(e["rule_id"])]
        r.level = float(e["to_level"])
        r.active = True
        r.triggered = False
        r.note = f"One exit per holding: {e['k']:g}x its monthly volatility ({e['vol_pct']:g}%)."[:160]
        event(e, "edited", f"Set by 'One exit per holding': {e['ticker']} trailing stop {e['to_level']:g}%",
              {"from": e["from_level"], "to": e["to_level"], "k": e["k"], "vol_pct": e["vol_pct"]})
        set_.append({"ticker": e["ticker"], "from": e["from_level"], "to": e["to_level"]})
    for c in spec.get("create") or []:
        made = await create_rule(session, user, ticker=c["ticker"], rule_type="trailing_stop", mode="pct",
                                 level=float(c["level"]),
                                 note=f"One exit per holding: {c['k']:g}x its monthly volatility ({c['vol_pct']:g}%).")
        event({"rule_id": str(made.id), "ticker": c["ticker"], "rule_type": "trailing_stop"}, "created",
              f"Added by 'One exit per holding': {c['ticker']} trailing stop {c['level']:g}%",
              {"level": c["level"], "k": c["k"], "vol_pct": c["vol_pct"]})
        added.append({"ticker": c["ticker"], "level": c["level"]})
    await session.commit()
    return {"ok": True, "rules_removed": removed, "rules_set": set_, "rules_added": added,
            "note": (f"Done: {len(removed)} rule(s) removed, {len(set_) + len(added)} trailing stop(s) set. "
                     f"Your goal's numbers update after the next solve.")}
