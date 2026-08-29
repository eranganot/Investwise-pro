"""Currency normalization: value every holding in the portfolio's base currency.

Holdings trade in their listing currency (US tickers in USD, Tel-Aviv in ILS,
London in GBP, ...). Without conversion, summing raw prices mixes currencies and
understates a USD-heavy book when the base currency is ILS. This module maps a
holding to its trading currency and converts amounts to the base currency via the
FX provider (cached/guarded). It fails safe: if a rate can't be fetched it falls
back to 1.0 so valuation never crashes.
"""
from __future__ import annotations

from app.core.config import get_settings

# exchange / market code -> trading currency
MARKET_CCY = {
    "TASE": "ILS", "NYSE": "USD", "NASDAQ": "USD", "AMEX": "USD", "US": "USD",
    "LSE": "GBP", "XETRA": "EUR", "EURONEXT": "EUR", "SIX": "CHF", "TSX": "CAD",
    "JPX": "JPY", "HKEX": "HKD", "SSE": "CNY", "NSE": "INR", "ASX": "AUD",
    "B3": "BRL", "SPOT": "USD",
}


def price_currency(market: str | None, meta: dict | None = None) -> str:
    """Best-effort trading currency: an explicit meta stamp wins, else the market."""
    if meta and meta.get("price_currency"):
        return str(meta["price_currency"]).upper()
    return MARKET_CCY.get((market or "").upper(), "USD")


def fx_rate(ccy: str, base: str | None = None) -> float:
    base = (base or get_settings().base_currency).upper()
    ccy = (ccy or base).upper()
    if ccy == base:
        return 1.0
    try:
        from app.providers.registry import guarded_fx
        return float(guarded_fx(ccy, base).rate)
    except Exception:
        return 1.0


def to_base(amount, ccy: str, base: str | None = None):
    if amount is None:
        return amount
    return amount * fx_rate(ccy, base)


# --- one definition of what a holding is worth -------------------------------
# Five call sites computed `quantity * current_price * fx_rate(...)` inline. The
# formula is not the hard part; agreeing on it is. A money invariant that
# measures the book with a different expression from the one the user is shown
# is not an invariant, so the check and the screen read the same function.


def position_value_ils(p, base: str | None = None) -> float:
    """ORM Position -> value in the base currency."""
    meta = p.meta if isinstance(p.meta, dict) else None
    return (float(p.quantity) * float(p.current_price or 0.0)
            * fx_rate(price_currency(p.market, meta), base))


def book_nav_ils(rows, base: str | None = None) -> float:
    return sum(position_value_ils(p, base) for p in rows)
