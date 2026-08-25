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


def classify(ticker: str, market: str, asset_class: str | None = None) -> str:
    if asset_class:
        return asset_class
    t = (ticker or "").upper()
    if t in FIXED_INCOME_TICKERS or any(k in t for k in ("BOND", "BND", "AGG", "GOV", "GILT")):
        return "Fixed Income"
    if (t in COMMODITY_TICKERS or market == "SPOT"
            or any(k in t for k in ("GOLD", "OIL", "SILVER"))):
        return "Commodities"
    return "Equities"


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
        cls = classify(p.ticker, p.market, (p.meta or {}).get("asset_class"))
        mix[cls] = round(mix.get(cls, 0.0) + _val(p) / nav, 4)
    return mix, nav
