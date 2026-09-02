"""Shared asset-class classification + objective targets for mix/rebalance."""
from __future__ import annotations

OBJ_TARGET = {
    "Grow": {"Equities": 0.80, "Fixed Income": 0.10, "Commodities": 0.10},
    "Balanced": {"Equities": 0.60, "Fixed Income": 0.30, "Commodities": 0.10},
    "Preserve": {"Equities": 0.30, "Fixed Income": 0.60, "Cash": 0.10},
    "Income": {"Equities": 0.40, "Fixed Income": 0.50, "Commodities": 0.10},
}


# The commodity ETFs the screener actually returns. Without these, `classify`
# fell through to "Equities" for every one of them -- so `_redeploy_cash_recs`
# looked for a leg whose class was "Commodities", never found one however many
# commodity ideas the screener produced, and the leg landed in `unplaced` every
# time. The Today screen showed COW, NIB and USO as ideas it could not act on,
# which reads as the app having no opinion rather than as a lookup miss.
COMMODITY_TICKERS = frozenset({
    "DBC", "DBA", "DBB", "DBE", "DBO", "DBP", "DBS",      # broad + sector baskets
    "GLD", "IAU", "SLV", "PPLT", "PALL",                   # metals
    "USO", "UNG", "BNO", "UGA",                            # energy
    "CORN", "WEAT", "SOYB", "CANE", "COW", "NIB", "JO",    # agriculture
    "PDBC", "COMT", "GSG", "FTGC", "BCI",                  # broad commodity funds
})

# THE SAME BUG THE COMMODITY SET ABOVE WAS ADDED TO FIX, one asset class over.
# `classify` falls through to "Equities" for anything it does not recognise, so
# a bond ETF missing from this set is bought, held, and counted against the
# EQUITY target -- the redeploy leg looks filled while the Fixed Income gap it
# was meant to close is untouched. Eleven of the funds in `fixed_income.CATALOG`
# were missing here (VGSH, VGIT, VGLT, USFR, SCHP, VTIP, VCSH, VCIT, USHY, EMB,
# VTEB): every Vanguard and Schwab ticker, i.e. all the cheap ones.
FIXED_INCOME_TICKERS = frozenset({
    "BND", "AGG", "TIP", "SHY", "IEF", "TLT", "LQD", "HYG", "BIL", "SHV", "MUB",
    "BNDW", "BNDX", "SCHZ", "SPAB", "FBND",                       # aggregate
    "GOVT", "VGSH", "VGIT", "VGLT", "SCHR", "SCHO", "TLH",        # government
    "SGOV", "USFR", "TFLO",                                       # cash-like
    "SCHP", "VTIP", "STIP",                                       # inflation-linked
    "VCSH", "VCIT", "IGSB", "SPSB", "BSV", "BIV", "BLV",          # corporate
    "USHY", "JNK", "SHYG",                                        # high yield
    "EMB", "VWOB", "PCY", "VTEB",                                 # EM / municipal
})


# A stored `asset_class` is only authoritative when a human PUT it there.
# `update_position` stamps this alongside it; nothing else may write it.
ASSET_CLASS_SOURCE_USER = "user"


def _from_tables(ticker: str, market: str | None) -> str | None:
    """The class the two tables above know for this ticker, or None.

    Split out of `classify` so the tables can be consulted WITHOUT the
    "Equities" fallthrough. "I do not recognise this ticker" and "this ticker is
    an equity" are different answers, and collapsing them is what let a default
    outrank a lookup below.
    """
    t = (ticker or "").upper()
    if t in FIXED_INCOME_TICKERS or any(k in t for k in ("BOND", "BND", "AGG", "GOV", "GILT")):
        return "Fixed Income"
    if (t in COMMODITY_TICKERS or market == "SPOT"
            or any(k in t for k in ("GOLD", "OIL", "SILVER"))):
        return "Commodities"
    return None


def classify(ticker: str, market: str, asset_class: str | None = None, *,
             asset_class_source: str | None = None) -> str:
    """The asset class of one holding. The TABLE outranks an unconfirmed default.

    THE BUG THIS FIXES. `buy_into_book` defaulted `asset_class="Equities"` and
    `_execute_funded_sleeve` never passed one, so every holding the sleeve
    funder bought was stamped "Equities" on the way in -- including BND. This
    function then returned that stamp before ever reading `FIXED_INCOME_TICKERS`
    four lines below, where "BND" has sat since the fix for exactly this failure
    one asset class over. The result on a live book: 2,842 ILS of bonds, 13.7%
    of NAV, reported as equity, `/mix` showing Fixed Income at **0%**, and a
    rebalance card with the sign wrong on both legs -- it asked to buy the whole
    fixed-income target because it believed the holding was zero.

    So the precedence is now: a class a HUMAN set wins (that is an override and
    the app has no business second-guessing it); otherwise the table wins;
    otherwise whatever was stored, which is only ever a default; otherwise
    Equities. Cash is decided first and by itself -- the synthetic CASH row
    carries `asset_class: "Cash"` in `CASH_META` and no table knows the ticker,
    so letting the tables run first would file the user's shekels as an equity.

    No data migration accompanies this. The stale "Equities" stamps stay on the
    rows and simply stop being authoritative, which is a strictly smaller change
    than rewriting a user's holdings in place.
    """
    ac = str(asset_class or "").strip()
    if ac.lower() == "cash":
        return "Cash"
    if ac and str(asset_class_source or "").strip().lower() == ASSET_CLASS_SOURCE_USER:
        return ac
    known = _from_tables(ticker, market)
    if known:
        return known
    return ac or "Equities"


def classify_holding(row) -> str:
    """`classify` for a Position row or a holding dict, reading meta correctly.

    Every call site that had a Position in hand was spelling out
    `classify(p.ticker, p.market, (p.meta or {}).get("asset_class"))`, and the
    provenance flag would have had to be threaded through every one of them --
    which is how the next call site forgets. One helper, and a holding is
    classified the same way wherever it is read.
    """
    if isinstance(row, dict):
        def get(k, d=None):
            return row.get(k, d)
    else:
        def get(k, d=None):
            return getattr(row, k, d)
    meta = get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    return classify(get("ticker"), get("market"),
                    meta.get("asset_class", get("asset_class")),
                    asset_class_source=meta.get("asset_class_source",
                                                get("asset_class_source")))


def current_mix(rows) -> tuple[dict, float]:
    """rows: ORM Position objects -> (allocation dict by class, nav). FX-normalized to base ccy."""
    from app.services.fx import price_currency, fx_rate

    def _val(p):
        meta = p.meta if isinstance(p.meta, dict) else None
        rate = fx_rate(price_currency(p.market, meta))
        return float(p.quantity) * float(p.current_price or 0) * rate

    nav = sum(_val(p) for p in rows)
    mix: dict[str, float] = {}
    if not nav:
        return mix, 0.0
    for p in rows:
        cls = classify_holding(p)
        mix[cls] = round(mix.get(cls, 0.0) + _val(p) / nav, 4)
    return mix, nav
