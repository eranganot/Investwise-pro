"""The trade ledger -- what the money actually did.

Until this module existed, ``Transaction`` had **zero writers outside the
models**. Two consequences, both of which cost real money to diagnose:

* ``nav_history`` had no flows, so ``net_flow_between`` always returned 0 and
  every book change was attributed to the market. On 2026-08-22 that rendered as
  ``total_pct: +34.62`` on a book whose holdings had gained 4%.
* When an accept silently minted 7,388 ILS of positions there was nothing to
  reconstruct it from. Phase N's docstring names this exactly: *"grep for
  ``Transaction(`` across app/ outside the models returns ZERO hits -- the trade
  ledger has never been written -- so there is no record of what the book was
  worth on any past day, and no way to reconstruct one."*

**A ledger row is not a suggestion, a preview or a plan.** It is written only
where the book is actually changed, in the same transaction as the change, so
the two cannot disagree. If a write path can move a position and does not call
``record_leg``, that path is a hole in the ledger and the next reconstruction
will be as impossible as this one was.
"""
from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger("investwise.ledger")

BUY, SELL, ADJUST = "Buy", "Sell", "Adjust"


def _dec(v) -> Decimal:
    try:
        return Decimal(str(round(float(v or 0), 6)))
    except Exception:  # noqa: BLE001
        return Decimal("0")


async def record_leg(
    session, user, *, ticker: str, txn_type: str, quantity: float,
    price: float = 0.0, value_ils: float = 0.0, tax_ils: float = 0.0,
    market: str | None = None, position_id=None, source: str | None = None,
    note: str | None = None,
):
    """Record one leg. Never raises -- a failed ledger write must not cost a trade.

    Deliberately best-effort. The book is the system of record and the ledger is
    the account of how it got that way; losing a row is bad, but refusing a
    user's trade because the bookkeeping failed is worse. Failures are logged at
    WARNING with the leg attached so they are recoverable by hand.
    """
    from app.models.tables import Transaction

    if txn_type in (BUY, SELL) and float(quantity or 0) > 0 and (
            float(price or 0) <= 0 or float(value_ils or 0) <= 0):
        # PR 147: 140 of 440 production rows (08-29 .. 10-23) were written at
        # price 0 and/or ₪0 because the caller's result carried no `price` /
        # `value_ils`. The trade itself was right; the record was not. Still
        # written (the share count matters), but no longer silently.
        logger.error("ledger leg without a price or value: %s %s x%s price=%s value=%s (%s)",
                     txn_type, ticker, quantity, price, value_ils, source)
    try:
        row = Transaction(
            position_id=position_id,
            subject=getattr(user, "email", None),
            ticker=(ticker or "").upper() or None,
            market=market,
            txn_type=txn_type,
            quantity=_dec(quantity),
            price=_dec(price),
            value_ils=_dec(value_ils),
            tax_ils=_dec(tax_ils),
            source=(source or "")[:40] or None,
            note=(note or "")[:255] or None,
        )
        session.add(row)
        await session.flush()
        return row
    except Exception as exc:  # noqa: BLE001
        logger.warning("ledger write failed for %s %s x%s (%s): %s",
                       txn_type, ticker, quantity, source, exc)
        return None


async def record_legs(session, user, *, sold=None, bought=None, source=None, note=None):
    """Record a whole accept: its sells and its buys, in one call.

    `sold` / `bought` accept the shapes the apply handlers already build, so a
    call site does not have to reshape its own result to be recorded -- the more
    translation a ledger needs, the more likely a path skips it.
    """
    for leg in (sold or []):
        await record_leg(
            session, user, ticker=leg.get("ticker"), txn_type=SELL,
            quantity=leg.get("shares") or 0, price=leg.get("price") or 0,
            value_ils=leg.get("value_ils") or 0, tax_ils=leg.get("tax_ils") or 0,
            market=leg.get("market"), source=source, note=note)
    for leg in (bought or []):
        await record_leg(
            session, user, ticker=leg.get("ticker"), txn_type=BUY,
            quantity=leg.get("shares") or 0, price=leg.get("price") or 0,
            value_ils=(leg.get("amount_ils") or leg.get("value_ils")
                       or leg.get("ils") or 0),
            market=leg.get("market"), source=source, note=note)


async def net_flows_ils(session, user, *, after: str | None = None,
                        upto: str | None = None) -> float:
    """Signed EXTERNAL money in the window -- deposits and withdrawals only.

    A swap is not a flow. Selling V to buy SCHD moves nothing in or out of the
    book, and counting either leg would make a rebalance look like a deposit --
    which is the whole failure this ledger exists to prevent, in reverse. Only
    `Adjust` rows tagged as external corrections count, alongside the
    contributions ledger that already tracks real deposits.
    """
    from sqlalchemy import select

    from app.models.tables import Transaction

    q = select(Transaction).where(
        Transaction.subject == getattr(user, "email", None),
        Transaction.txn_type == ADJUST,
    )
    rows = (await session.execute(q)).scalars().all()
    total = 0.0
    for r in rows:
        d = (r.executed_at.date().isoformat() if r.executed_at else "")
        if after and not (after < d):
            continue
        if upto and not (d <= upto):
            continue
        total += float(r.value_ils or 0)
    return round(total, 2)


async def traded_dates(session, user) -> set:
    """The set of dates on which a Buy or Sell leg was recorded.

    For `nav_history`, which has one question to answer about every step where
    the position count changed: *was that a trade, or was it money?* A buy or a
    sell is money moving WITHIN the book, so a step containing one needs no
    estimation -- the recorded NAVs already describe it, and the difference
    between the two readings is what got reported to Eran as -208.56 ILS of
    deposits and withdrawals that never happened.

    Returned as a set of ISO dates rather than a predicate over rows, so the
    caller pays one query for the whole window instead of one per step.
    """
    from sqlalchemy import select

    from app.models.tables import Transaction

    q = select(Transaction).where(
        Transaction.subject == getattr(user, "email", None),
        Transaction.txn_type.in_((BUY, SELL)),
    )
    try:
        rows = (await session.execute(q)).scalars().all()
    except Exception as exc:  # noqa: BLE001
        # Best-effort, like every read here. Losing this degrades the chart to
        # the estimate it used before -- never to a wrong number.
        logger.warning("ledger: traded_dates unavailable: %s", exc)
        return set()
    return {r.executed_at.date().isoformat() for r in rows if r.executed_at}


async def recent(session, user, limit: int = 50) -> list[dict]:
    """The ledger as the API returns it."""
    from sqlalchemy import desc, select

    from app.models.tables import Transaction

    q = (select(Transaction)
         .where(Transaction.subject == getattr(user, "email", None))
         .order_by(desc(Transaction.executed_at)).limit(limit))
    rows = (await session.execute(q)).scalars().all()
    return [{
        "at": r.executed_at.isoformat() if r.executed_at else None,
        "ticker": r.ticker, "market": r.market, "side": r.txn_type,
        "quantity": float(r.quantity or 0), "price": float(r.price or 0),
        "value_ils": float(r.value_ils or 0), "tax_ils": float(r.tax_ils or 0),
        "source": r.source, "note": r.note,
    } for r in rows]


def last_buy(buys, p, price, _td) -> dict | None:
    """The last priced buy of `p` since the position was (re)opened (PR 118b: from routes/intake)."""
    tk = (p.ticker or "").upper()
    opened = p.created_at
    for t, px, when in buys:
        if (t or "").upper() != tk or when is None:
            continue
        if opened is not None:
            o = opened if opened.tzinfo or not when.tzinfo else opened.replace(tzinfo=when.tzinfo)
            w = when if when.tzinfo or not o.tzinfo else when.replace(tzinfo=o.tzinfo)
            if w < o - _td(seconds=60):
                return None
        px = float(px)
        return {"price": round(px, 4), "at": when.isoformat(),
                "change_pct": (round((price / px - 1) * 100, 2) if price and px else None)}
    return None
