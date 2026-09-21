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

  3. THE BOOKS. For a holding that was opened INSIDE the ledger (its opening
     buy is recorded), shares-in minus shares-out must equal today's count.
     That gives the EXACT total of the blind sells in that episode. One blind
     sell -> its size is exact. Several -> the estimates are scaled so they sum
     to the exact total ("balanced": the total is exact, the split between
     them is proportional to the estimates).

WHAT GETS WRITTEN, and only on `apply(confirm=True)`:

  * duplicates -> `txn_type = "Void"` with a note naming the row it duplicates.
    Every reader filters by Buy/Sell/Adjust, so a Void row is ignored, and it
    can be undone by setting the type back. Nothing is deleted.
  * blind sells -> `quantity` (and `value_ils`, `price` when known) with a note:
    method, confidence, and "was 0". Only EXACT and BALANCED values are written
    by default; ESTIMATE-only values need `include_estimates=True`; anything
    without a defensible value is left alone and listed.

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
# A scaled estimate further than this from its own estimate means the books and
# the estimates disagree about what happened -- flagged, never written.
_MAX_SCALE_DRIFT = 0.25


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

    # ---- estimates for each blind sell ------------------------------------
    rows: dict[str, dict] = {}
    for t in blind:
        when = _aware(t.executed_at)
        tk = (t.ticker or "").upper()
        ccy = price_currency(t.market, None)
        fx, fx_status = fx_rate_with_status(ccy)
        px, px_src = await offload(_price_at, tk, when) if when else (None, "no time")
        r = {"id": str(t.id), "ticker": tk, "at": when.isoformat() if when else None,
             "source": t.source, "card": _card_id(t.note), "price": px, "price_source": px_src,
             "fx": round(fx, 6), "estimates": {}, "shares": None, "confidence": "unresolved",
             "method": None}
        # 1. the rule event executed at the same moment
        ev = min((e for e in events if (e.ticker or "").upper() == tk and e.outcome_at
                  and abs(_aware(e.outcome_at) - when) <= _EVENT_WINDOW
                  and ((e.action or {}).get("executed") or {}).get("kind") in ("trim", None)),
                 key=lambda e: abs(_aware(e.outcome_at) - when), default=None) if when else None
        if ev is not None:
            exe = (ev.action or {}).get("executed") or {}
            gross = float(exe.get("cash_added_ils") or 0) + float(exe.get("tax_ils") or 0)
            if gross > 0 and px:
                r["estimates"]["rule_event"] = round(gross / (px * fx), 6)
                r["gross_ils"] = round(gross, 2)
            plan_sh = ((ev.action or {}).get("plan") or {}).get("shares")
            if plan_sh:
                r["estimates"]["rule_plan_shares"] = round(float(plan_sh), 6)
        # 2. the card's own amount
        amt = (cards.get(r["card"] or "") or {}).get("amount")
        if amt and px and t.source == "trim":
            r["estimates"]["card_amount"] = round(float(amt) / (px * fx), 6)
        if "rule_event" in r["estimates"]:
            r["estimate"], r["estimate_from"] = r["estimates"]["rule_event"], "rule_event"
        elif "card_amount" in r["estimates"]:
            r["estimate"], r["estimate_from"] = r["estimates"]["card_amount"], "card_amount"
        else:
            r["estimate"], r["estimate_from"] = None, None
        rows[r["id"]] = r

    # ---- the books: episodes opened inside the ledger ---------------------
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
        unknown_total = round(known - float(pos.quantity or 0), 6)
        ep = {"ticker": tk, "anchored": True, "opened": opened.isoformat(),
              "blind_sells": len(in_ep), "exact_total_sold": unknown_total,
              "residual": None}
        if not in_ep:
            ep["residual"] = unknown_total
            if abs(unknown_total) > 1e-3:
                ep["why"] = ("the recorded trades do not add up to today's count and no blind "
                             "sell is there to explain it -- a trade the app never recorded")
            episodes.append(ep)
            continue
        if unknown_total <= 0:
            ep["why"] = "the books leave nothing for the blind sells -- not repaired"
            episodes.append(ep)
            continue
        missing = [r for r in in_ep if r["estimate"] is None]
        if len(in_ep) == 1:
            r = in_ep[0]
            r.update(shares=unknown_total, confidence="exact", method="books (single blind sell)")
        elif len(missing) == 1:
            others = sum(r["estimate"] for r in in_ep if r["estimate"] is not None)
            rest = round(unknown_total - others, 6)
            for r in in_ep:
                if r["estimate"] is not None:
                    r.update(shares=r["estimate"], confidence="estimate", method=r["estimate_from"])
            if rest > 0:
                # Depends on the other sells' estimates, so it is no better than
                # they are: an estimate, not written by default.
                missing[0].update(shares=rest, confidence="estimate",
                                  method="books minus the other sells' estimates")
        elif not missing:
            est_sum = sum(r["estimate"] for r in in_ep)
            scale = unknown_total / est_sum if est_sum else 0
            ep["scale"] = round(scale, 4)
            if abs(scale - 1) <= _MAX_SCALE_DRIFT:
                for r in in_ep:
                    r.update(shares=round(r["estimate"] * scale, 6), confidence="balanced",
                             method=f"{r['estimate_from']} scaled x{scale:.3f} to the exact total")
            else:
                ep["why"] = (f"the estimates sum to {est_sum:.4f} but the books say "
                             f"{unknown_total:.4f} -- too far apart to trust either split")
        else:
            ep["why"] = f"{len(missing)} blind sells have no estimate -- the split cannot be made"
        episodes.append(ep)

    # ---- outside any anchored episode: estimates only ----------------------
    for r in rows.values():
        if r["shares"] is None and r["estimate"] is not None:
            r.update(shares=r["estimate"], confidence="estimate", method=r["estimate_from"])

    for r in rows.values():
        if r["shares"] and r.get("price"):
            r["value_ils"] = round(r["shares"] * r["price"] * r["fx"], 2)

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
        "writes_by_default": ["duplicates -> Void", "blind sells with confidence exact/balanced"],
    }


async def apply(session: AsyncSession, user: User, *, confirm: bool,
                include_estimates: bool = False) -> dict:
    """Write what `preview` proposes. Refuses without `confirm=True`."""
    if not confirm:
        return {"ok": False, "reason": "confirm is required -- run the preview first"}
    plan = await preview(session, user)
    ok_conf = {"exact", "balanced"} | ({"estimate"} if include_estimates else set())
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
        from app.models.tables import money
        t.quantity = money(r["shares"])
        if r.get("price"):
            t.price = money(r["price"])
        if r.get("value_ils"):
            t.value_ils = money(r["value_ils"])
        t.note = (f"{t.note or ''} | {REPAIR_TAG}: {r['confidence']} via {r['method']}; "
                  f"was 0 shares")[:255]
        written.append({"id": r["id"], "ticker": r["ticker"], "shares": r["shares"],
                        "confidence": r["confidence"]})
    await session.commit()
    return {"ok": True, "voided": voided, "written": written, "skipped": skipped,
            "summary": plan["summary"]}

