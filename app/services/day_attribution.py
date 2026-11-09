"""P9 -- which holdings moved the book on the latest session, in shekels.

The Today chart says how much the book moved; nothing said WHICH holdings moved
it. This answers that per holding: shares held now x the price move on the
latest session x today's FX, so the parts add up to the book's market move.

WHICH SESSION, SAID PLAINLY. Each holding's move is measured on ITS latest
session, and the row says which: "today so far" when the quote is newer than the
last daily close (the market is open), otherwise "last session" with its date.
On 2026-11-05 the chart's "Today so far" showed -1.12% before the US market had
opened -- the week to date, under a label that said today. This does not repeat
that: a closed market reports the last session it actually had, named.

WHAT IT IS NOT. Shares are today's count, so a holding bought or sold during the
session is attributed as if held all session (`traded_today` flags it). FX is
today's spot, as `position_history` and `performance_service` use, and a move
in the shekel itself is not split out. Cash and unpriced or stale holdings are
skipped and listed. Never written to the database. Not financial advice.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _day(v) -> str:
    return str(v)[:10]


def move_for(points: list, *, current_price: float, price_as_of: str | None,
             ccy: str) -> dict | None:
    """One holding's move on its latest session, in its price currency.

    ``points`` are daily closes, oldest first, as `guarded_history` returns them.
    Pure: no provider, no database.
    """
    from app.services.position_history import _agorot_scale

    pts = [(_day(d), float(c)) for d, c in (points or []) if c]
    if not pts or not current_price or current_price <= 0:
        return None
    k = _agorot_scale(pts, current_price, ccy)
    pts = [(d, c * k) for d, c in pts]
    quote_day = _day(price_as_of) if price_as_of else None
    last_day, last_close = pts[-1]
    if quote_day and quote_day > last_day:
        # The quote is newer than the last close: the market is (or was) open today.
        prev, now, session, basis = last_close, float(current_price), quote_day, "today so far"
    else:
        if len(pts) < 2:
            return None
        prev, now, session, basis = pts[-2][1], last_close, last_day, "last session"
    if prev <= 0:
        return None
    return {"move": now - prev, "move_pct": (now / prev - 1.0) * 100.0,
            "session": session, "basis": basis}


async def day_movers(session, user) -> dict:
    from app.providers.registry import guarded_history
    from app.services.portfolio_view import holdings

    view = await holdings(session, user)
    rows, skipped = [], []
    today = datetime.now(timezone.utc).date().isoformat()
    traded = await _traded_today(session, user, today)

    async def _one(p):
        tk = str(p.get("ticker") or "").upper()
        if not tk or tk == "CASH" or str(p.get("asset_class")) == "Cash":
            return None
        if p.get("price_stale") or not p.get("current_price") or not p.get("current_price_ils"):
            skipped.append({"ticker": tk, "reason": "no fresh price"})
            return None
        try:
            pts = await asyncio.to_thread(guarded_history, tk, 10)
        except Exception as exc:  # noqa: BLE001
            skipped.append({"ticker": tk, "reason": f"no daily closes ({type(exc).__name__})"})
            return None
        m = move_for(pts, current_price=float(p["current_price"]),
                     price_as_of=p.get("price_as_of"), ccy=str(p.get("currency") or ""))
        if m is None:
            skipped.append({"ticker": tk, "reason": "not enough daily closes"})
            return None
        fx = float(p["current_price_ils"]) / float(p["current_price"])
        move_ils = float(p.get("quantity") or 0.0) * m["move"] * fx
        return {"ticker": tk, "value_ils": round(float(p.get("value_ils") or 0.0), 2),
                "move_ils": round(move_ils, 2), "move_pct": round(m["move_pct"], 2),
                "session": m["session"], "basis": m["basis"],
                "traded_today": tk in traded}

    for r in await asyncio.gather(*[_one(p) for p in view.get("positions") or []]):
        if r:
            rows.append(r)
    rows.sort(key=lambda r: -abs(r["move_ils"]))
    total = round(sum(r["move_ils"] for r in rows), 2)
    base = sum(r["value_ils"] for r in rows) - total
    sessions = sorted({r["session"] for r in rows})
    return {"ok": True, "rows": rows, "total_ils": total,
            "total_pct": round(total / base * 100.0, 2) if base > 0 else None,
            "sessions": sessions,
            "basis": sorted({r["basis"] for r in rows}),
            "skipped": skipped,
            "note": ("Shares held now x each holding's move on its latest session x today's FX. "
                     "A holding traded today is counted as if held all session.")}


async def _traded_today(session, user, today: str) -> set[str]:
    """Tickers with a recorded trade today (UTC)."""
    from sqlalchemy import select

    from app.models.tables import Transaction
    start = datetime.fromisoformat(today).replace(tzinfo=timezone.utc)
    try:
        tickers = (await session.execute(select(Transaction.ticker).where(
            Transaction.subject == user.email, Transaction.executed_at >= start))).scalars().all()
    except Exception:  # noqa: BLE001
        return set()
    return {str(t or "").upper() for t in tickers if t}
