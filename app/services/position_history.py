"""One holding's chart: what its price did, and what YOUR position in it did.

Eran, 2026-09-20: "I want to add a graph per holding to see trends (per day or
week or month)." Chosen: both lines with a toggle, defaulting to the position's
value; range tabs 1D, 1W, 1M, 3M, 1Y, All.

TWO DIFFERENT QUESTIONS, TWO DIFFERENT SOURCES OF TRUTH.

* **Price** -- the ticker's own quotes. Any range, back to ten years.
* **Your position** -- shares you actually held at each moment x the price.
  Shares are reconstructed BACKWARDS from today's count through the trade
  ledger (``transactions``): every recorded buy, sell and reconcile adjustment
  after a moment is undone to get the count at that moment. It is NOT today's
  share count priced back through the past -- that is the backfill Phase N
  removed from the Today chart, because it answers "what would this position
  have done", not "what did my money do".

  So the value line starts where the record starts: the later of when this
  position was opened and when the ledger began recording (2026-08, the day
  ``Transaction`` got its first writer). Before that there is no record of how
  many shares were held, and the line is simply not drawn. ``starts_at`` says
  where, and ``starts_because`` says why.

  The change is split into what the MARKET did (shares held x price moves) and
  what TRADES did (buys add value that is not a gain), so a top-up never reads
  as a return.

CASH IS REFUSED BEFORE ANY PROVIDER CALL. "CASH" is also Pathward Financial on
NASDAQ; see tests/test_cash_is_never_sent_to_the_market.py.

FX is today's spot, held constant, as ``performance_service`` does and for the
same reason (no historical FX series exists). Reported as ``fx_basis: "spot"``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.offload import offload
from app.models.tables import Transaction, User

logger = logging.getLogger("investwise.position_history")

# range -> (kind, span-or-days)
RANGES = {"1D": ("intraday", "1d"), "1W": ("intraday", "5d"), "1M": ("daily", 23),
          "3M": ("daily", 66), "1Y": ("daily", 253), "ALL": ("daily", 2600)}
_MAX_POINTS = 400
_BUY, _SELL, _ADJUST = "Buy", "Sell", "Adjust"


class NotChartable(ValueError):
    """A request this endpoint must refuse (cash, not held, unknown range)."""


def _ts(v) -> datetime:
    """A point's time as an aware datetime. A bare date is that day's close."""
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v)
    if len(s) == 10:
        return datetime.fromisoformat(s + "T23:59:59+00:00")
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _signed(leg) -> float:
    q = float(leg.quantity or 0)
    if leg.txn_type == _SELL:
        return -abs(q)
    if leg.txn_type == _BUY:
        return abs(q)
    return q                      # Adjust is already a signed delta (after - before)


def _downsample(points: list) -> list:
    n = len(points)
    if n <= _MAX_POINTS:
        return points
    step = n / _MAX_POINTS
    idx = sorted({int(i * step) for i in range(_MAX_POINTS)} | {n - 1})
    return [points[i] for i in idx]


def _fetch_prices(ticker: str, rng: str) -> tuple[list[tuple[str, float]], str]:
    """Blocking. Returns (points, granularity)."""
    from app.providers.registry import guarded_history, guarded_intraday

    kind, arg = RANGES[rng]
    if kind == "intraday":
        try:
            return guarded_intraday(ticker, arg), ("5min" if arg == "1d" else "30min")
        except Exception as exc:  # noqa: BLE001 -- say so, fall back to closes
            logger.info("intraday unavailable for %s (%s); using daily closes", ticker, exc)
            days = 2 if arg == "1d" else 6
            return guarded_history(ticker, 30)[-days:], "daily_fallback"
    return guarded_history(ticker, int(arg)), "daily"


def _agorot_scale(points, current_price: float | None, ccy: str) -> float:
    """Yahoo quotes Tel Aviv in agorot and says so only in a currency code the
    history endpoint does not hand back. Anchor on the book's own price: if the
    last close is ~100x it, the series is in agorot."""
    if ccy != "ILS" or not points or not current_price:
        return 1.0
    ratio = float(points[-1][1]) / float(current_price)
    return 0.01 if 60.0 <= ratio <= 160.0 else 1.0


async def position_history(session: AsyncSession, user: User, ticker: str,
                           rng: str = "1M") -> dict:
    from app.core.config import get_settings
    from app.services.fx import fx_rate_with_status, price_currency
    from app.services.intake_service import is_cash_position, list_positions

    rng = (rng or "1M").upper()
    if rng not in RANGES:
        raise NotChartable(f"range must be one of {', '.join(RANGES)}")
    tk = (ticker or "").strip().upper()
    rows = await list_positions(session, user)
    pos = next((p for p in rows if (p.ticker or "").upper() == tk), None)
    if pos is None:
        raise NotChartable(f"{tk or 'that'} is not one of your holdings")
    meta = pos.meta if isinstance(pos.meta, dict) else None
    if is_cash_position(pos.ticker, meta):
        raise NotChartable("cash has no price history -- it is worth its face value every day")

    base = (getattr(get_settings(), "base_currency", None) or "ILS").upper()
    ccy = price_currency(pos.market, meta)
    rate, fx_status = fx_rate_with_status(ccy, base)

    raw, granularity = await offload(_fetch_prices, pos.ticker, rng)
    scale = _agorot_scale(raw, float(pos.current_price or 0) or None, ccy)
    # Full resolution first: the value line is clipped to where the record
    # starts, and downsampling a ten-year series to 400 points BEFORE clipping
    # left one or two points after a position opened in September (observed on
    # production, SOXL range=ALL: "no record yet" for a 7-day-old position).
    full = [(str(t), round(float(c) * scale, 6)) for t, c in raw if c]
    prices = _downsample(full)
    if len(prices) < 2:
        raise NotChartable(f"not enough price history for {tk} over {rng}")

    # ---- the record: legs for this ticker, and where the ledger begins ------
    legs = list((await session.execute(
        select(Transaction).where(Transaction.subject == user.email,
                                  func.upper(Transaction.ticker) == tk,
                                  Transaction.txn_type.in_([_BUY, _SELL, _ADJUST]))
        .order_by(Transaction.executed_at.asc()))).scalars().all())
    ledger_start = (await session.execute(
        select(func.min(Transaction.executed_at)).where(
            Transaction.subject == user.email))).scalar()
    opened = pos.created_at
    candidates = [_ts(x) for x in (opened, ledger_start) if x is not None]
    since = max(candidates) if candidates else None
    starts_because = ("the position was opened then" if (opened and since == _ts(opened))
                      else "the trade ledger starts then -- share counts before it were never recorded")

    current_qty = float(pos.quantity or 0)
    leg_pts = [(_ts(x.executed_at), _signed(x), x) for x in legs if x.executed_at is not None]

    def shares_at(t: datetime) -> float:
        return current_qty - sum(q for when, q, _x in leg_pts if when > t)

    value_pts, reconciles, why_not = [], True, None
    # A SALE WITH NO SHARE COUNT CANNOT BE UNDONE. Trims were written to the
    # ledger as 0-share sells until PR 68.1; those rows are still there, and
    # rebuilding the count through them draws a line that is simply wrong.
    blind = [x for when, q, x in leg_pts
             if x.txn_type == _SELL and abs(q) < 1e-9 and (since is None or when >= since)]
    if blind:
        reconciles = False
        why_not = (f"{len(blind)} recorded sale(s) of {tk} have no share count "
                   f"(trims before PR 68.1), so how many shares you held on past days "
                   f"cannot be rebuilt")
    # OPENED INSIDE THE RECORD. Just before it opened the count must be either
    # ~zero (the opening buy is in the ledger) or exactly the count just after
    # opening (entered by hand, no opening leg) -- and never negative. Anything
    # else means a trade is missing or counted twice. A minute's slack either
    # side: the opening leg and the position row are written in one request,
    # not at the same microsecond.
    if reconciles and since is not None and opened is not None and since == _ts(opened):
        before = shares_at(since - timedelta(seconds=60))
        after = shares_at(since + timedelta(seconds=60))
        tol = max(1e-3, 0.005 * max(abs(after), abs(current_qty), 1e-9))
        if before < -tol or (abs(before) > tol and abs(before - after) > tol):
            reconciles = False
            why_not = (f"undoing every recorded trade leaves {before:+.4f} shares before the "
                       f"position opened (expected 0 or {after:.4f}) -- a trade is missing "
                       f"or recorded twice")
    for t, px in full:
        when = _ts(t)
        if since is not None and when < since:
            continue
        sh = shares_at(when)
        value_pts.append({"t": t, "shares": round(sh, 6), "price": px,
                          "value": round(sh * px * rate, 2)})

    if reconciles and any(v["shares"] < -1e-6 for v in value_pts):
        reconciles = False
    value_pts = _downsample(value_pts)
    market_change = 0.0
    for a, b in zip(value_pts, value_pts[1:]):
        market_change += a["shares"] * (b["price"] - a["price"]) * rate
    value_change = (value_pts[-1]["value"] - value_pts[0]["value"]) if len(value_pts) > 1 else 0.0

    # YOUR BUYS, independent of the range: every priced buy since this position
    # was (re)opened, newest first -- the timing detail Eran asked to see here
    # rather than on the holdings list (2026-09-21). Identical rows at the same
    # second are collapsed: `fund_sleeve` recorded sleeve buys twice until PR
    # 68.1, and those duplicates are still in the ledger.
    cur_px = float(pos.current_price or 0) or None
    buys, seen = [], set()
    open_from = (_ts(opened) - timedelta(seconds=60)) if opened is not None else None
    for when, q, x in reversed(leg_pts):
        if x.txn_type != _BUY or not float(x.price or 0):
            continue
        if open_from is not None and when < open_from:
            break
        key = (when.replace(microsecond=0), round(abs(q), 6), round(float(x.price), 4))
        if key in seen:
            continue
        seen.add(key)
        px = float(x.price)
        buys.append({"at": when.isoformat(), "shares": round(abs(q), 6), "price": round(px, 4),
                     "change_pct": round((cur_px / px - 1) * 100, 2) if cur_px else None})
        if len(buys) >= 8:
            break

    first_t = _ts(prices[0][0])
    markers = [{"t": when.isoformat(), "type": x.txn_type, "shares": round(abs(q), 6),
                "price": float(x.price or 0) or None, "source": x.source}
               for when, q, x in leg_pts if when >= first_t]

    p0, p1 = prices[0][1], prices[-1][1]
    out = {
        "ok": True, "ticker": pos.ticker, "market": pos.market, "range": rng,
        "granularity": granularity, "currency": ccy, "base_currency": base,
        "fx_rate": round(rate, 6), "fx_basis": "spot", "fx_status": fx_status,
        "shares_now": current_qty,
        "price": {"points": [{"t": t, "price": px} for t, px in prices],
                  "first": p0, "last": p1,
                  "change_pct": round((p1 / p0 - 1) * 100, 2) if p0 else None},
        "value": {
            "available": len(value_pts) >= 2 and reconciles,
            "points": value_pts if reconciles else [],
            "starts_at": since.isoformat() if since else None,
            "starts_because": starts_because if since else None,
            "clipped": bool(since and first_t < since),
            "change_ils": round(value_change, 2),
            "market_change_ils": round(market_change, 2),
            "trades_change_ils": round(value_change - market_change, 2),
        },
        "trades": markers,
        "buys": buys,
        "avg_price": round(float(pos.cost_basis or 0), 4) or None,
        "current_price": cur_px,
    }
    if not reconciles:
        out["value"]["reason"] = (
            (why_not or "the recorded trades do not reconcile with today's share count "
                        "(a hand edit that the ledger never saw)")
            + " -- showing the price only rather than a share count that cannot be right")
    elif len(value_pts) < 2:
        out["value"]["reason"] = (f"no record yet in this range: the line starts "
                                  f"{since.date().isoformat() if since else 'later'}, "
                                  f"because {starts_because}")
    return out
