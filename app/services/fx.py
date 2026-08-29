"""Currency normalization: value every holding in the portfolio's base currency.

Holdings trade in their listing currency (US tickers in USD, Tel-Aviv in ILS,
London in GBP, ...). Without conversion, summing raw prices mixes currencies and
understates a USD-heavy book when the base currency is ILS. This module maps a
holding to its trading currency and converts amounts to the base currency via the
FX provider (cached/guarded).

TWO THINGS THIS MODULE GOT WRONG, both worth reading before changing it:

* A quoted currency code is not always the currency. Tel Aviv quotes in agorot
  and reports "ILA"; London quotes in pence and reports "GBp". See MINOR_UNITS.
* "Fails safe to 1.0" was the docstring for years and it is not safe. Valuing
  one unit of an unknown currency as one shekel is how agorot became shekels.
  A failure now keeps the last rate that actually resolved, and an unknown code
  is logged at ERROR rather than answered with a number nobody can check.
"""
from __future__ import annotations

import logging

from app.core.config import get_settings

logger = logging.getLogger("investwise.fx")

# Last rate that actually resolved, per (ccy, base). A stale real rate beats a
# fabricated 1.0 by orders of magnitude, and this is the only state this module
# keeps; it is per-process and deliberately never persisted.
_LAST_GOOD_RATE: dict[tuple[str, str], float] = {}

# exchange / market code -> trading currency
MARKET_CCY = {
    "TASE": "ILS", "NYSE": "USD", "NASDAQ": "USD", "AMEX": "USD", "US": "USD",
    "LSE": "GBP", "XETRA": "EUR", "EURONEXT": "EUR", "SIX": "CHF", "TSX": "CAD",
    "JPX": "JPY", "HKEX": "HKD", "SSE": "CNY", "NSE": "INR", "ASX": "AUD",
    "B3": "BRL", "SPOT": "USD",
}


# --- minor units: the 100x trap ---------------------------------------------
#
# Several exchanges quote in a MINOR unit and say so in the CURRENCY CODE rather
# than in the price. Yahoo, verified live on 2026-08-29:
#
#     TEVA.TA  currency ILA  price  11030.0   <- agorot.   110.30 ILS
#     POLI.TA  currency ILA  price   7761.0   <- agorot.    77.61 ILS
#     ESLT.TA  currency ILA  price 211190.0   <- agorot.  2111.90 ILS
#     VOD.L    currency GBp  price    118.45  <- pence.      1.1845 GBP
#     AAPL     currency USD  price    319.70  <- major unit, no trap
#
# `pricing_service` stamped `price_currency` straight from the quote, so the
# position carried "ILA". Nothing downstream knows that code: the builtin FX
# provider has no ILA pair and returns 1.0 WITHOUT ERRORING, and the live one
# raises and `fx_rate` below fails safe to 1.0. Either way agorot were valued as
# shekels, and the holding was worth ONE HUNDRED TIMES what it is -- in NAV, in
# gain %, in the allocation mix, in the concentration caps, and in every card
# sized against NAV.
#
# This needs no TASE feature work to happen. The holdings form already offers
# Tel Aviv and London (`index.html`), so a book can carry one of these today.
#
# CASE MATTERS. "GBp" (pence) upper-cases to "GBP" (pounds) -- the major
# currency it is one hundredth of. Uppercasing before the lookup turns the
# detection into a no-op for exactly the currency that most needs it, so the raw
# code is matched first and "GBP" is excluded from the case-insensitive pass.
MINOR_UNITS: dict[str, tuple[str, float]] = {
    "GBp": ("GBP", 100.0),      # pence
    "GBX": ("GBP", 100.0),      # pence, the other spelling
    "ILA": ("ILS", 100.0),      # agorot
    "ZAc": ("ZAR", 100.0),      # South African cents
}


def major_unit(currency: str | None) -> tuple[str, float]:
    """(major currency code, minor units per major). Never raises.

    ``major_unit("ILA") -> ("ILS", 100.0)``, ``major_unit("USD") -> ("USD", 1.0)``.
    """
    raw = (currency or "").strip()
    if raw in MINOR_UNITS:
        return MINOR_UNITS[raw]
    upper = raw.upper()
    if upper != "GBP":          # GBP is a real major currency, not pence
        for code, pair in MINOR_UNITS.items():
            if code.upper() == upper:
                return pair
    return (upper or "USD", 1.0)


def to_major_units(price, currency: str | None) -> tuple[float, str]:
    """Convert a quoted price into its major unit. Use at every boundary.

    At the boundary rather than at the thirteen call sites that ask for a rate:
    a normalisation each consumer has to remember is one a new consumer silently
    opts out of.
    """
    ccy, per_major = major_unit(currency)
    if price is None:
        return price, ccy
    price = float(price)
    return (price / per_major if per_major != 1.0 else price), ccy


def price_currency(market: str | None, meta: dict | None = None) -> str:
    """Best-effort trading currency: an explicit meta stamp wins, else the market.

    Always returns a MAJOR currency code. A row written before this fix can still
    carry "ILA"; normalising here means consumers ask for a rate that exists
    rather than one that silently resolves to 1.0. The stored PRICE on such a row
    is still 100x - `pricing_service` repairs that on the next refresh. This half
    only stops the wrong currency code spreading.
    """
    if meta and meta.get("price_currency"):
        return major_unit(str(meta["price_currency"]))[0]
    return MARKET_CCY.get((market or "").upper(), "USD")


def fx_rate_with_status(ccy: str, base: str | None = None) -> tuple[float, str]:
    """(rate, status) converting `ccy` into `base`. Status is the point.

    THE 1.0 FALLBACK IS NOT SAFE AND IS NOT A DEFAULT. It was reached in two very
    different situations and only one of them is survivable:

      * the provider wobbled on a currency it knows (USD/ILS). Falling back to
        1.0 does not "fail safe" - it values a USD book at 27% of its worth. The
        last rate that actually resolved is a far better answer than 1, so one is
        remembered per pair and served with a warning.
      * the code is not a currency the provider has ever heard of, which is how
        "ILA" got valued at par. That is not a wobble, it is a wrong input, and
        it now says so at ERROR instead of returning a number.

    It still RETURNS rather than raises, because thirteen call sites do
    arithmetic on the result and turning this into an exception is a change to
    all of them. What it no longer does is fail silently.

    STATUS EXISTS BECAUSE THE VALUE COULD NOT CARRY THE MEANING.
    `performance_service` decided a rate was missing by testing `r == 1.0` --
    the only signal there was. That cannot tell a real peg from a failure, and
    it certainly cannot tell either from a stale-but-real rate. Callers that
    need to report on the quality of a conversion read the status; callers doing
    plain arithmetic keep using `fx_rate` and are unaffected.

      "live"       the provider answered now.
      "stale"      the provider failed; this is the last rate that resolved.
                   The number is real and roughly right. The reading is not
                   current and anything reporting on it should say so.
      "unresolved" the provider failed and nothing was ever cached, so this is
                   the 1.0 that means "no idea". Never present it as a value.
    """
    base = (base or get_settings().base_currency).upper()
    ccy, _ = major_unit(ccy or base)
    if ccy == base:
        return 1.0, "live"
    try:
        from app.providers.registry import guarded_fx
        rate = float(guarded_fx(ccy, base).rate)
    except Exception as exc:  # noqa: BLE001 -- valuation must not crash on FX
        last = _LAST_GOOD_RATE.get((ccy, base))
        if last is not None:
            logger.warning("fx: %s/%s unavailable (%s); using the last rate that "
                           "resolved, %.6f", ccy, base, type(exc).__name__, last)
            return last, "stale"
        logger.error("fx: NO RATE FOR %s/%s (%s) and none was ever cached. "
                     "Valuing 1 %s as 1 %s, which is almost certainly wrong. "
                     "If %s is not a currency code, that is the bug.",
                     ccy, base, type(exc).__name__, ccy, base, ccy)
        return 1.0, "unresolved"
    if rate == 1.0 and ccy != base:
        # The builtin provider answers every unknown pair with 1.0 and no error,
        # so a wrong currency code looked exactly like a legitimate peg. Say it.
        logger.warning("fx: %s/%s resolved to exactly 1.0 - check that %s is a "
                       "currency code the provider knows", ccy, base, ccy)
    _LAST_GOOD_RATE[(ccy, base)] = rate
    return rate, "live"


def fx_rate(ccy: str, base: str | None = None) -> float:
    """The rate alone, for the call sites that only do arithmetic with it."""
    return fx_rate_with_status(ccy, base)[0]


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
