"""Repair the trade ledger rows written wrong before PR 68.1 / 68.3.

Eran, 2026-09-21: "yes, you should repair the old ledger rows".

WHAT IS WRONG (all observed on production, see STATUS PR 68.1 / 68.3):

  * DUPLICATES -- a sleeve funded from a card wrote each leg twice at the same
    second: once as `sleeve_fund` (by `_execute_funded_sleeve`) and once as
    `fund_sleeve` (by `_record_apply`).
  * BLIND SELLS -- sells written with quantity 0: every `trim` (the handler
    reported no share count) and every `core_to_basket` sale.
  * TRUNCATED SELLS -- `buy_funded` wrote `int(shares)`. The fraction is gone
    and nothing recorded it; those rows are reported, never guessed at.

HOW EACH BLIND SELL IS SIZED -- two independent estimates, then the books:

  1. RULE EVENT. A trim fired by a trading rule left a `rule_events` row whose
     `action.executed` holds `cash_added_ils` and `tax_ils`. Gross = cash + tax.
  2. CARD AMOUNT. Every accepted card left `{amount}` in the `completed_recs`
     KV map, keyed by the card id the ledger row names in its note. That is
     the card's own estimate of the sale in shekels when it was built.
  Either amount becomes shares at the price of the nearest 30-minute bar at or
  before the sale, converted at today's FX (no historical FX series exists --
  the same limit `performance_service` states; about +/-2% over three weeks).

  3. THE BOOKS -- a CHECK, never a source. For a holding opened inside the ledger,
     buys minus known sells minus today's count is what the blind sells should
     add up to. It was going to be the anchor ("exact total, split by the
     estimates"). The first production preview (2026-09-21) killed that: in 3
     of the 5 anchored holdings (AGG +1.69, GOOGL +1.04, QQQ +0.04 shares) the
     book holds MORE than its recorded trades explain, with no blind sell to
     blame -- some write path still changes positions without a ledger row. An
     anchor that is itself missing trades would have scaled every SOXL trim by
     0.83 and written the result with a confident label. So the books total is
     reported beside the estimates, and nothing is derived from it.

WHAT GETS WRITTEN, and only on `apply(confirm=True)`:

  * duplicates -> `txn_type = "Void"` with a note naming the row it duplicates.
    Every reader filters by Buy/Sell/Adjust, so a Void row is ignored, and it
    can be undone by setting the type back. Nothing is deleted.
  * blind sells -> `quantity` (and `value_ils`, `price` when known) with a note:
    method, confidence, and "was 0". MEASURED values (a rule event: cash the
    book actually received plus tax, AND within 5% of the share count the rule
    itself planned) are written by default; ESTIMATE values
    (the card's own pre-trade amount) only with `include_estimates=True`;
    anything else is left alone and listed.

The preview is the product: it names every row, the value it would write,
and exactly where that value came from. Not financial advice.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import KVSetting, RuleEvent, Transaction, User

logger = logging.getLogger("investwise.ledger_repair")

REPAIR_TAG = "repaired 2026-09-21"
_DUP_SOURCES = {"sleeve_fund", "fund_sleeve"}
_DUP_WINDOW = timedelta(seconds=5)
_EVENT_WINDOW = timedelta(seconds=180)
# A measured size is trusted only when the rule's own planned share count agrees
# with it this closely; beyond that, the two records of one sale contradict.
_AGREE = 0.05


def _aware(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _card_id(note: str | None) -> str | None:
    n = (note or "").strip()
    return n[5:].strip() if n.startswith("card ") else None


def _signed(t: Transaction) -> float:
    q = float(t.quantity or 0)
    if t.txn_type == "Sell":
        return -abs(q)
    if t.txn_type == "Buy":
        return abs(q)
    if t.txn_type == "Adjust":
        return q
    return 0.0


def _price_at(ticker: str, when: datetime) -> tuple[float | None, str]:
    """Blocking. The last 30-minute bar at or before `when`, else that day's close."""
    from app.providers.registry import guarded_history, guarded_intraday

    try:
        bars = guarded_intraday(ticker, "60d")
        best = None
        for t, px in bars:
            ts = _aware(datetime.fromisoformat(str(t)))
            if ts <= when:
                best = float(px)
            else:
                break
        if best:
            return best, "30-min bar"
    except Exception as exc:  # noqa: BLE001 -- fall back to the daily close
        logger.info("intraday unavailable for %s: %s", ticker, exc)
    try:
        day = when.date().isoformat()
        closes = [(d, c) for d, c in guarded_history(ticker, 260) if str(d)[:10] <= day]
        if closes:
            return float(closes[-1][1]), "daily close"
    except Exception as exc:  # noqa: BLE001
        logger.info("history unavailable for %s: %s", ticker, exc)
    return None, "no price"


async def _load(session: AsyncSession, user: User) -> dict:
    txs = list((await session.execute(
        select(Transaction).where(Transaction.subject == user.email)
        .order_by(Transaction.executed_at.asc()))).scalars().all())
    events = list((await session.execute(
        select(RuleEvent).where(RuleEvent.subject == user.email,
                                RuleEvent.outcome == "executed"))).scalars().all())
    kv = await session.get(KVSetting, f"completed_recs:{user.email}")
    cards: dict = {}
    if kv is not None:
        try:
            raw = json.loads(kv.value)
            if isinstance(raw, dict):
                cards = {k: (v if isinstance(v, dict) else {}) for k, v in raw.items()}
        except Exception:  # noqa: BLE001
            cards = {}
    from app.services.intake_service import list_positions
    positions = await list_positions(session, user)
    return {"txs": txs, "events": events, "cards": cards, "positions": positions}


def _duplicates(txs: list[Transaction]) -> list[dict]:
    out, used = [], set()
    by = sorted((t for t in txs if t.txn_type in ("Buy", "Sell") and t.executed_at),
                key=lambda t: _aware(t.executed_at))
    for i, a in enumerate(by):
        if a.id in used or (a.source or "") not in _DUP_SOURCES:
            continue
        for b in by[i + 1:]:
            if _aware(b.executed_at) - _aware(a.executed_at) > _DUP_WINDOW:
                break
            if (b.id not in used and (b.ticker or "") == (a.ticker or "")
                    and b.txn_type == a.txn_type
                    and {a.source, b.source} == _DUP_SOURCES
                    and abs(float(a.quantity or 0) - float(b.quantity or 0)) < 1e-6
                    and abs(float(a.price or 0) - float(b.price or 0)) < 1e-4):
                keep, void = (a, b) if a.source == "sleeve_fund" else (b, a)
                used |= {a.id, b.id}
                out.append({"void_id": str(void.id), "keep_id": str(keep.id),
                            "ticker": void.ticker, "side": void.txn_type,
                            "shares": float(void.quantity or 0),
                            "at": _aware(void.executed_at).isoformat(),
                            "reason": "the same leg recorded twice (sleeve_fund + fund_sleeve)"})
                break
    return out


async def preview(session: AsyncSession, user: User) -> dict:
    """READ-ONLY. Every row the repair would change, the value, and why."""
    from app.core.offload import offload
    from app.services.fx import fx_rate_with_status, price_currency

    data = await _load(session, user)
    txs, events, cards, positions = data["txs"], data["events"], data["cards"], data["positions"]
    dups = _duplicates(txs)
    voided = {d["void_id"] for d in dups}

    ledger_start = min((_aware(t.executed_at) for t in txs if t.executed_at), default=None)
    blind = [t for t in txs if t.txn_type == "Sell" and t.ticker
             and abs(float(t.quantity or 0)) < 1e-9 and str(t.id) not in voided
             and REPAIR_TAG not in (t.note or "")]
    truncated = [t for t in txs if t.txn_type == "Sell" and (t.source or "") == "buy_funded"
                 and float(t.quantity or 0) > 0
                 and abs(float(t.quantity) - round(float(t.quantity))) < 1e-9]

    # ---- price and card amount for each blind sell -------------------------
    rows: dict[str, dict] = {}
    for t in blind:
        when = _aware(t.executed_at)
        tk = (t.ticker or "").upper()
        ccy = price_currency(t.market, None)
        fx, _fx_status = fx_rate_with_status(ccy)
        px, px_src = await offload(_price_at, tk, when) if when else (None, "no time")
        r = {"id": str(t.id), "ticker": tk, "at": when.isoformat() if when else None,
             "source": t.source, "card": _card_id(t.note), "price": px, "price_source": px_src,
             "fx": round(fx, 6), "estimates": {}, "shares": None, "confidence": "unresolved",
             "method": None, "_when": when}
        amt = (cards.get(r["card"] or "") or {}).get("amount")
        if amt and px and t.source == "trim":
            r["estimates"]["card_amount"] = round(float(amt) / (px * fx), 6)
        rows[r["id"]] = r

    # ---- rule events: each one sizes AT MOST ONE row, nearest first ---------
    # The first preview matched one 15:43 event to trims at 15:42 AND 15:43.
    # Rows an earlier apply already sized stay in the pairing as consumers, so the
    # assignment replays exactly as it was when they were written. Without them
    # their events look free again: on production (2026-09-21) the repaired SOXL
    # 15:43 trim released its 15:43:15 event to the 15:42 trim, and a second
    # apply would have sold those shares twice.
    repaired = [t for t in txs if t.txn_type == "Sell" and t.ticker and t.executed_at
                and REPAIR_TAG in (t.note or "") and "was 0 shares" in (t.note or "")]
    consumers = [(r["id"], r["ticker"], r["_when"]) for r in rows.values()
                 if r["_when"] is not None]
    consumers += [(str(t.id), (t.ticker or "").upper(), _aware(t.executed_at))
                  for t in repaired]
    pairs = []
    for cid, tk, when in consumers:
        for e in events:
            if (e.ticker or "").upper() != tk or not e.outcome_at:
                continue
            exe = (e.action or {}).get("executed") or {}
            if exe.get("kind") not in ("trim", None):
                continue
            dt = abs(_aware(e.outcome_at) - when)
            if dt <= _EVENT_WINDOW:
                pairs.append((dt, cid, e))
    used_rows, used_events = set(), set()
    for dt, rid, e in sorted(pairs, key=lambda x: x[0]):
        if rid in used_rows or e.id in used_events:
            continue
        used_rows.add(rid)
        used_events.add(e.id)
        if rid not in rows:          # an already-repaired row: it keeps its event
            continue
        r = rows[rid]
        exe = (e.action or {}).get("executed") or {}
        gross = float(exe.get("cash_added_ils") or 0) + float(exe.get("tax_ils") or 0)
        if gross > 0 and r["price"]:
            r["estimates"]["rule_event"] = round(gross / (r["price"] * r["fx"]), 6)
            r["gross_ils"] = round(gross, 2)
            r["rule_event_at"] = _aware(e.outcome_at).isoformat()
        plan_sh = ((e.action or {}).get("plan") or {}).get("shares")
        if plan_sh:
            r["estimates"]["rule_plan_shares"] = round(float(plan_sh), 6)

    for r in rows.values():
        r.pop("_when", None)
        ev_sh = r["estimates"].get("rule_event")
        plan_sh = r["estimates"].get("rule_plan_shares")
        if ev_sh and plan_sh and abs(ev_sh / plan_sh - 1) > _AGREE:
            # Two records of one sale that disagree. A card can shrink between
            # the firing and the accept, so the gap alone does not say which is
            # right -- and a size nobody can defend is not written by default.
            # Seen on production: MSFT 0.70, V 0.77 (both 2026-08-25, while the
            # FX handling was still being fixed), SOXL 09-08 0.87.
            r.update(shares=ev_sh, confidence="estimate",
                     method=(f"rule event, but the rule planned {plan_sh:g} shares "
                             f"({ev_sh / plan_sh:.2f}x) -- the two records disagree"))
        elif ev_sh:
            r.update(shares=ev_sh, confidence="measured",
                     method=("rule event: cash received + tax, at the price that half hour"
                             + (f"; agrees with the rule's planned {plan_sh:g} shares"
                                if plan_sh else "")))
        elif "card_amount" in r["estimates"]:
            r.update(shares=r["estimates"]["card_amount"], confidence="estimate",
                     method="the card's own amount before the trade")
        if r["shares"] and r.get("price"):
            r["value_ils"] = round(r["shares"] * r["price"] * r["fx"], 2)

    # ---- the books: a CHECK on each holding opened inside the ledger --------
    episodes = []
    for pos in positions:
        tk = (pos.ticker or "").upper()
        if tk == "CASH" or pos.created_at is None or ledger_start is None:
            continue
        opened = _aware(pos.created_at)
        legs = [t for t in txs if (t.ticker or "").upper() == tk and t.executed_at
                and _aware(t.executed_at) >= opened - timedelta(seconds=90)
                and str(t.id) not in voided and t.txn_type in ("Buy", "Sell", "Adjust")]
        opening = [t for t in legs if t.txn_type == "Buy"
                   and abs(_aware(t.executed_at) - opened) <= timedelta(seconds=90)]
        if opened < ledger_start or not opening:
            episodes.append({"ticker": tk, "anchored": False,
                             "why": "opened before the ledger, or by hand with no opening buy"})
            continue
        known = sum(_signed(t) for t in legs if str(t.id) not in rows)
        in_ep = [rows[str(t.id)] for t in legs if str(t.id) in rows]
        books = round(known - float(pos.quantity or 0), 6)
        sized = round(sum(r["shares"] or 0 for r in in_ep), 6)
        ep = {"ticker": tk, "anchored": True, "opened": opened.isoformat(),
              "blind_sells": len(in_ep), "books_say_sold": books,
              "repair_would_size": sized,
              "unsized_blind_sells": sum(1 for r in in_ep if not r["shares"])}
        if not in_ep and abs(books) > 1e-3:
            ep["why"] = ("today's count and the recorded trades disagree with no blind sell "
                         "to explain it -- a write path that records nothing")
        episodes.append(ep)

    by_conf: dict[str, int] = {}
    for r in rows.values():
        by_conf[r["confidence"]] = by_conf.get(r["confidence"], 0) + 1
    return {
        "ok": True, "read_only": True,
        "summary": {"duplicates": len(dups), "blind_sells": len(rows), **by_conf,
                    "truncated_funded_sells": len(truncated)},
        "duplicates": dups,
        "blind_sells": sorted(rows.values(), key=lambda r: r["at"] or ""),
        "episodes": episodes,
        "truncated_funded_sells": [
            {"id": str(t.id), "ticker": t.ticker, "shares": float(t.quantity),
             "at": _aware(t.executed_at).isoformat() if t.executed_at else None,
             "note": "recorded as a whole number by int(); the true fraction was never stored"}
            for t in truncated],
        "unrecorded_rebalances": await unrecorded_rebalances(
            session, user, data, voided, set(rows), ledger_start),
        "writes_by_default": ["duplicates -> Void", "blind sells with confidence 'measured'"],
        "writes_on_request": ["blind sells with confidence 'estimate' (include_estimates)",
                              "unrecorded rebalances -> Adjust legs (include_rebalances)"],
    }


async def apply(session: AsyncSession, user: User, *, confirm: bool,
                include_estimates: bool = False, include_rebalances: bool = False) -> dict:
    """Write what `preview` proposes. Refuses without `confirm=True`."""
    if not confirm:
        return {"ok": False, "reason": "confirm is required -- run the preview first"}
    plan = await preview(session, user)
    ok_conf = {"measured"} | ({"estimate"} if include_estimates else set())
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    voided, written, skipped = [], [], []

    import uuid
    for d in plan["duplicates"]:
        t = await session.get(Transaction, uuid.UUID(d["void_id"]))
        if t is None or t.txn_type == "Void":
            continue
        t.note = (f"{t.note or ''} | {REPAIR_TAG}: Void (was {t.txn_type}); "
                  f"duplicate of {d['keep_id']} at {stamp}")[:255]
        t.txn_type = "Void"
        voided.append(d["void_id"])
    for r in plan["blind_sells"]:
        if r["confidence"] not in ok_conf or not r["shares"] or r["shares"] <= 0:
            skipped.append({"id": r["id"], "ticker": r["ticker"], "confidence": r["confidence"]})
            continue
        t = await session.get(Transaction, uuid.UUID(r["id"]))
        if t is None or abs(float(t.quantity or 0)) > 1e-9:
            continue
        from app.models.tables import db_money
        t.quantity = db_money(r["shares"])
        if r.get("price"):
            t.price = db_money(r["price"])
        if r.get("value_ils"):
            t.value_ils = db_money(r["value_ils"])
        t.note = (f"{t.note or ''} | {REPAIR_TAG}: {r['confidence']} via {r['method']}; "
                  f"was 0 shares")[:255]
        written.append({"id": r["id"], "ticker": r["ticker"], "shares": r["shares"],
                        "confidence": r["confidence"]})
    rebalanced = []
    if include_rebalances:
        # One Adjust per holding per rebalance step, dated AT the accept, so the
        # position chart replays the change where it happened. value_ils stays 0:
        # an Adjust with a value is read as external money by `net_flows_ils`,
        # and a rebalance moved no money in or out.
        from app.models.tables import db_money
        for a in (plan.get("unrecorded_rebalances") or {}).get("adjusts", []):
            row = Transaction(
                subject=user.email, ticker=a["ticker"], txn_type="Adjust",
                quantity=db_money(a["shares"]), source="rebalance_repair",
                executed_at=datetime.fromisoformat(a["at"]),
                note=(f"{REBAL_TAG}: x{a['factor']} ({a['confidence']}; factor from "
                      f"{', '.join(a['factor_from'])}) {'; '.join(a['cards'])}")[:255])
            session.add(row)
            rebalanced.append({"ticker": a["ticker"], "shares": a["shares"], "at": a["at"],
                               "confidence": a["confidence"]})
    await session.commit()
    return {"ok": True, "voided": voided, "written": written, "skipped": skipped,
            "rebalances_written": rebalanced, "summary": plan["summary"]}



# --------------------------------------------------------------------------- #
# PR 73 -- rebalances that moved shares and recorded nothing
# --------------------------------------------------------------------------- #
REBAL_TAG = "rebalance reconstructed 2026-09-28"
_REBAL_CLASSES = ("Equities", "Fixed Income", "Commodities", "Cash", "Real Estate",
                  "Crypto", "Alternatives")


def _rebalance_card_ids() -> set[str]:
    """Every card id whose Accept ran `_rebalance_to` before PR 73.

    Card ids are content hashes (`recommendations._rid`), so the set is exact:
    one per class, every merged combination `_reconcile` can build, and the
    behind-your-goal "shift to Grow" card (`set_objective_and_rebalance`).
    """
    from itertools import combinations

    from app.services.recommendations import _rid
    ids = {_rid("rebal", c) for c in _REBAL_CLASSES}
    for n in range(2, len(_REBAL_CLASSES) + 1):
        for combo in combinations(_REBAL_CLASSES, n):
            ids.add(_rid("rebal_merged", *sorted(combo)))
    ids.add(_rid("behind", "grow"))
    return ids


async def _rebalance_accepts(session: AsyncSession, txs: list) -> list[dict]:
    """Rebalance accepts that recorded no trade, oldest first.

    From `audit_log` -- every request the app served, which is the only record of
    WHEN a pre-PR 73 rebalance ran (it wrote nothing else). The audit log is not
    per-user; this app has one owner, and the preview names every accept it used
    so that assumption is checkable. An accept that DID record legs (PR 73
    onwards: source `rebalance_to_objective` within a minute) is not unrecorded
    and is skipped.
    """
    from app.models.tables import AuditLog

    ids = _rebalance_card_ids()
    rows = (await session.execute(
        select(AuditLog.created_at, AuditLog.route)
        .where(AuditLog.method == "POST", AuditLog.route.like("/api/v1/recommendations/%/accept"))
        .order_by(AuditLog.created_at.asc()))).all()
    recorded = [_aware(t.executed_at) for t in txs
                if (t.source or "") in ("rebalance_to_objective", "set_objective_and_rebalance")
                and t.executed_at]
    out = []
    for at, route in rows:
        card = route.split("/")[-2]
        if card not in ids:
            continue
        at = _aware(at)
        if any(abs((r - at).total_seconds()) <= 60 for r in recorded):
            continue
        out.append({"card": card, "at": at})
    return out


def _simulate(legs: list, events: list, factors: dict, cls: str) -> float:
    """Share count today, replaying legs and rebalances in time order."""
    q = 0.0
    timeline = ([(_aware(t.executed_at), 0, t) for t in legs]
                + [(g["at"], 1, g) for g in events])
    for _when, kind, item in sorted(timeline, key=lambda x: (x[0], x[1])):
        if kind == 0:
            q += _signed(item)
        else:
            q *= factors.get((item["key"], cls), 1.0)
    return q


async def unrecorded_rebalances(session: AsyncSession, user: User, data: dict,
                                voided: set, blind_ids: set,
                                ledger_start) -> dict:
    """Reconstruct the share change each unrecorded rebalance made, per holding.

    A rebalance multiplied every holding in a class by ONE factor. So for a
    holding opened inside the ledger with no blind sell in its episode, today's
    count is a known function of its recorded legs and the factors of the
    rebalances it lived through. A holding that lived through exactly one
    unknown factor SOLVES it; every other holding in that class that lived
    through the same rebalance must AGREE, or nothing is written. Measured on
    production 2026-09-28: 09-26 13:14:49 Equities from SOXL 1.002681 and VTI
    1.002685, Commodities from USO and UGA both 1.0295.

    Rebalances with no leg of the holding between them cannot be told apart and
    are merged into one step (their factors multiply); the note names each.
    Nothing here writes -- `apply(include_rebalances=True)` does.
    """
    from app.services.allocation_mix import classify_holding

    txs, positions = data["txs"], data["positions"]
    accepts = await _rebalance_accepts(session, txs)
    episodes = []
    for pos in positions:
        tk = (pos.ticker or "").upper()
        if tk == "CASH" or pos.created_at is None or ledger_start is None:
            continue
        opened = _aware(pos.created_at)
        if opened < ledger_start:
            continue
        legs = [t for t in txs if (t.ticker or "").upper() == tk and t.executed_at
                and _aware(t.executed_at) >= opened - timedelta(seconds=90)
                and str(t.id) not in voided and t.txn_type in ("Buy", "Sell", "Adjust")]
        if not any(t.txn_type == "Buy" and abs(_aware(t.executed_at) - opened)
                   <= timedelta(seconds=90) for t in legs):
            continue
        if any(str(t.id) in blind_ids for t in legs):
            continue                                 # a blind sell already muddies it
        if any(REBAL_TAG in (t.note or "") or REPAIR_TAG in (t.note or "") for t in legs):
            # Already repaired, or carrying a blind sell sized by the 09-21
            # repair: an ESTIMATED leg must not solve a factor another holding's
            # history is then rewritten with. Clean episodes only.
            continue
        evs = [a for a in accepts if a["at"] > opened]
        if not evs:
            continue
        # merge rebalances with no leg of this holding between them
        groups, cur = [], []
        leg_times = sorted(_aware(t.executed_at) for t in legs)
        for a in evs:
            if cur and any(cur[-1]["at"] < lt < a["at"] for lt in leg_times):
                groups.append(cur)
                cur = []
            cur.append(a)
        if cur:
            groups.append(cur)
        events = [{"at": g[-1]["at"], "key": tuple(x["card"] + "@" + x["at"].isoformat()
                                                   for x in g), "members": g}
                  for g in groups]
        episodes.append({"ticker": tk, "class": classify_holding(pos), "legs": legs,
                         "events": events, "now": float(pos.quantity or 0),
                         "opened": opened})

    factors: dict = {}
    sources: dict = {}
    progress = True
    while progress:
        progress = False
        for ep in episodes:
            unknown = [e for e in ep["events"] if (e["key"], ep["class"]) not in factors]
            if len(unknown) != 1:
                continue
            key = (unknown[0]["key"], ep["class"])
            f1 = _simulate(ep["legs"], ep["events"], {**factors, key: 1.0}, ep["class"])
            f2 = _simulate(ep["legs"], ep["events"], {**factors, key: 2.0}, ep["class"])
            a = f2 - f1
            if abs(a) < 1e-9:
                continue
            factors[key] = 1.0 + (ep["now"] - f1) / a
            sources[key] = [ep["ticker"]]
            progress = True
    # AGREEMENT: every other holding that lived through a solved step -- but only
    # a holding that SOLVED NOTHING itself is an independent check. A holding
    # that solved one of its own steps USING a factor has absorbed any error in
    # that factor into its own solve, and then reproduces its count by
    # construction. Review of this PR: 0.3 unrecorded VTI shares made QQQ
    # "confirm" VTI's poisoned 09-26 factor and both were written as measured.
    solvers = {src[0] for src in sources.values() if src}
    disagree = []
    for ep in episodes:
        if ep["ticker"] in solvers:
            continue
        for e in ep["events"]:
            key = (e["key"], ep["class"])
            if key not in factors or ep["ticker"] in sources.get(key, []):
                continue
            unknown = [x for x in ep["events"] if (x["key"], ep["class"]) not in factors]
            if unknown:
                continue
            got = _simulate(ep["legs"], ep["events"], factors, ep["class"])
            # 2e-4 shares: every count is stored at 4 decimals, so two holdings
            # replaying one factor can differ by a rounding step each.
            if abs(got - ep["now"]) <= max(2e-4, 1e-5 * ep["now"]):
                sources[key].append(ep["ticker"])
            else:
                disagree.append({"ticker": ep["ticker"], "step": list(e["key"]),
                                 "expected_now": round(got, 6), "now": ep["now"]})

    # A DISAGREEMENT VOIDS THE FACTOR FOR EVERYONE. Review of this PR: marking
    # only the disagreeing holding left the holding that SOLVED the factor free
    # to write it -- and which one solves first is just the order positions come
    # back in, so the written one could be the wrong one. Every holding that
    # lived through a disputed step is left alone.
    bad_keys = set()
    for ep in episodes:
        for d in disagree:
            if d["ticker"] == ep["ticker"]:
                bad_keys |= {(e["key"], ep["class"]) for e in ep["events"]}
    for k in bad_keys:
        factors.pop(k, None)
    adjusts, unresolved = [], []
    for ep in episodes:
        unknown = [e for e in ep["events"] if (e["key"], ep["class"]) not in factors]
        recorded = sum(_signed(t) for t in ep["legs"])
        gap = round(ep["now"] - recorded, 6)
        if abs(gap) <= 1e-6 and not unknown:
            continue                                   # nothing to repair
        if unknown or any((e["key"], ep["class"]) in bad_keys for e in ep["events"]):
            unresolved.append({"ticker": ep["ticker"], "gap_shares": gap,
                               "why": ("a rebalance step no holding could solve, or holdings "
                                       "disagree on its factor")})
            continue
        # per-step share change = count just before the step x (factor - 1)
        q = 0.0
        timeline = ([(_aware(t.executed_at), 0, t) for t in ep["legs"]]
                    + [(e["at"], 1, e) for e in ep["events"]])
        rows = []
        for _when, kind, item in sorted(timeline, key=lambda x: (x[0], x[1])):
            if kind == 0:
                q += _signed(item)
                continue
            f = factors[(item["key"], ep["class"])]
            delta = q * (f - 1.0)
            q *= f
            if abs(delta) <= 1e-6:
                continue
            src = sources.get((item["key"], ep["class"]), [])
            rows.append({"ticker": ep["ticker"], "asset_class": ep["class"],
                         "at": item["at"].isoformat(), "shares": round(delta, 6),
                         "factor": round(f, 6),
                         "cards": [m["card"] + " " + m["at"].isoformat(timespec="seconds")
                                   for m in item["members"]],
                         "factor_from": src,
                         "confidence": "measured" if len(src) >= 2 else "exact_total"})
        residual = round(gap - sum(r["shares"] for r in rows), 6)
        if abs(residual) > 2e-4 or not rows:
            unresolved.append({"ticker": ep["ticker"], "gap_shares": gap,
                               "why": "the steps do not add up to the gap"})
            continue
        # A factor shared across holdings reproduces each one only to the 4th
        # decimal each count is stored at. The repair must close THIS holding's
        # gap exactly, or its chart stays off by the remainder -- so the rounding
        # remainder (at most 2e-4 shares, said so in the row) joins the last step.
        if residual:
            rows[-1]["shares"] = round(rows[-1]["shares"] + residual, 6)
            rows[-1]["rounding_shares"] = residual
        adjusts.extend(rows)
    return {"accepts": [{"card": a["card"], "at": a["at"].isoformat()} for a in accepts],
            "adjusts": adjusts, "unresolved": unresolved, "disagreements": disagree,
            "note": ("confidence 'measured' = the factor was solved by one holding and "
                     "reproduced by another in the same class; 'exact_total' = the "
                     "holding's total gap is exact, and its steps were solved from itself")}


# --------------------------------------------------------------------------- #
# PR 86 -- held-cash "invest elsewhere" buys that reached the book, not the ledger
# --------------------------------------------------------------------------- #
# Eran, 2026-09-30 00:52: "add a repair that fix those missing entries". PR 83's
# held-cash accept returned before `_record_apply`, so its buys changed the book
# and wrote nothing (production: VTI 620, USO 270, BND 270; cash 1,863.49 ->
# 703.49, zero rows). The AMOUNTS are known exactly (the cash that left); the
# SHARES are what the accept computed: amount / (quote at that moment x FX). The
# accept ran after the US close, so the quote was the close -- `_price_at` at the
# accept time returns that bar -- and FX is today's rate, the same limit every
# repair here states. Measured check on production: BND 270 / (70.23 x 3.0561)
# = 1.2580 and 15.1303 - 1.2580 = 13.8723, the ledger's own 07:42 basket buy,
# to four decimals.
HELD_CASH_TAG = "held-cash buy reconstructed (PR 86)"


def parse_legs(spec: str) -> list[dict]:
    """"VTI:620,USO:270" -> [{"ticker": "VTI", "amount_ils": 620.0}, ...]."""
    out = []
    for part in [x for x in (spec or "").split(",") if x.strip()]:
        tk, _, amt = part.partition(":")
        out.append({"ticker": tk.strip().upper(), "amount_ils": round(float(amt), 2)})
    return out


async def _accepted_at(session: AsyncSession, user: User, card: str) -> datetime | None:
    kv = await session.get(KVSetting, f"completed_recs:{user.email}")
    if not kv:
        return None
    try:
        mark = (json.loads(kv.value) or {}).get(card)
    except Exception:  # noqa: BLE001
        return None
    if mark is None:
        return None
    ts = mark.get("at") if isinstance(mark, dict) else mark
    try:
        return _aware(datetime.fromisoformat(str(ts)))
    except Exception:  # noqa: BLE001
        return None


async def held_cash_preview(session: AsyncSession, user: User, *, card: str,
                            legs: list[dict], at: str | None = None) -> dict:
    """READ-ONLY. The Buy rows a held-cash accept should have written."""
    return await _held_cash_plan(session, user, card=card, legs=legs, at=at)


async def _held_cash_plan(session, user, *, card, legs, at):
    import asyncio

    from app.services.fx import fx_rate, price_currency
    from app.services.intake_service import list_positions
    when = (_aware(datetime.fromisoformat(at)) if at else await _accepted_at(session, user, card))
    if when is None:
        return {"ok": False, "reason": f"no accept time for {card}: pass at=<ISO time>"}
    rows = await list_positions(session, user)
    held: dict[str, dict] = {}
    for p in rows:
        k = (p.ticker or "").upper()
        meta = p.meta if isinstance(p.meta, dict) else {}
        h = held.setdefault(k, {"qty": 0.0, "currency": price_currency(p.market, meta),
                                "market": p.market, "px": None, "px_at": None})
        h["qty"] += float(p.quantity or 0)
        if p.current_price and meta.get("price_as_of") and h["px"] is None:
            h["px"], h["px_at"] = float(p.current_price), str(meta.get("price_as_of"))
    done = {(t.ticker or "").upper() for t in (await session.execute(
        select(Transaction).where(Transaction.subject == user.email,
                                  Transaction.txn_type == "Buy",
                                  Transaction.note.like(f"%{HELD_CASH_TAG}%{card}%")))).scalars()}
    out = []
    for lg in legs:
        tk, amt = lg["ticker"], float(lg["amount_ils"])
        h = held.get(tk)
        row = {"ticker": tk, "amount_ils": amt, "at": when.isoformat()}
        if tk in done:
            out.append({**row, "status": "already recorded"})
            continue
        if not h or h["qty"] <= 0:
            out.append({**row, "status": "not held - nothing to record"})
            continue
        # The EXACT quote the accept used, when it is still on the position: a
        # price stamped before the accept and within the day before it (the
        # market closed at 20:00 UTC and the accept ran after) has not been
        # refreshed since. Otherwise the last 30-minute bar at the accept.
        px, src = None, ""
        try:
            _pa = _aware(datetime.fromisoformat(h["px_at"])) if h.get("px_at") else None
        except Exception:  # noqa: BLE001
            _pa = None
        if h.get("px") and _pa is not None and _pa <= when and when - _pa <= timedelta(hours=20):
            px, src = h["px"], f"position quote as of {h['px_at']}"
        else:
            px, src = await asyncio.to_thread(_price_at, tk, when)
        fx = float(fx_rate(h.get("currency") or "USD") or 0.0)
        if not px or fx <= 0:
            out.append({**row, "status": f"no price ({src})"})
            continue
        shares = round(amt / (px * fx), 4)
        if shares > h["qty"] + 1e-9:
            out.append({**row, "status": f"{shares} shares is more than the {h['qty']} held"})
            continue
        out.append({**row, "status": "to write", "shares": shares, "price": round(px, 4),
                    "price_source": src, "fx": round(fx, 4), "market": h["market"],
                    "held_now": round(h["qty"], 4), "held_before": round(h["qty"] - shares, 4)})
    return {"ok": True, "read_only": True, "card": card, "at": when.isoformat(), "legs": out,
            "to_write": sum(1 for x in out if x["status"] == "to write")}


async def held_cash_apply(session: AsyncSession, user: User, *, card: str, legs: list[dict],
                          at: str | None = None, confirm: bool = False) -> dict:
    """Write the preview's "to write" rows, dated AT the accept. Idempotent."""
    if not confirm:
        return {"ok": False, "reason": "confirm is required -- run the preview first"}
    plan = await _held_cash_plan(session, user, card=card, legs=legs, at=at)
    if not plan.get("ok"):
        return plan
    from app.models.tables import db_money
    written = []
    for x in plan["legs"]:
        if x["status"] != "to write":
            continue
        session.add(Transaction(
            subject=user.email, ticker=x["ticker"], market=x.get("market"), txn_type="Buy",
            quantity=db_money(x["shares"]), price=db_money(x["price"]), value_ils=db_money(x["amount_ils"]),
            source="held_cash_redeploy", executed_at=datetime.fromisoformat(x["at"]),
            note=(f"{HELD_CASH_TAG}: card {card}; {x['amount_ils']} ILS / "
                  f"({x['price']} x FX {x['fx']}, {x['price_source']})")[:255]))
        written.append({k: x[k] for k in ("ticker", "shares", "amount_ils", "at")})
    await session.commit()
    return {"ok": True, "written": written, "legs": plan["legs"]}
