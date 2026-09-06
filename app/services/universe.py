"""Candidate universe for the Opportunity (screener) agent.

A curated, liquid set the screener ranks across: large-cap US stocks (sector-
diversified), broad/sector ETFs, Tel-Aviv (TASE) listings, Israeli companies
listed in the US, plus every commodity in the commodity catalog. Kept
deliberately bounded so a live provider can fetch fundamentals for the whole set
within the resilience budget.

Each entry: ticker, name, market, asset_class, kind (stock | etf | commodity).

**THE TEL AVIV SECTION USED TO BE A LABEL WITH NOTHING BEHIND IT.** Until PR 62
this docstring said "Tel-Aviv (TASE) names" and the list underneath was headed
``# ---- Tel-Aviv (TASE) names (Yahoo .TA suffix) ----`` -- and every one of its
seven entries was ``"market": "NASDAQ"`` or ``"NYSE"`` with no ``.TA`` suffix.
They were the US ADRs of Israeli companies. So the app could not recommend a
single instrument that trades in Tel Aviv, on a book denominated in shekels and
taxed under Israeli CGT, while its own source said otherwise.

Everything else was already built for it and had been for weeks:
``MARKET_CCY["TASE"] = "ILS"``, the ``.TA`` symbol path, the ILA/agorot
minor-unit detection and the 100x row repair. Only the candidate list was
missing, which is the part that decides what can ever be suggested.

**What is here and what is not.** TASE *shares* are fully served -- all 17 below
returned price, currency ILA, sector, P/E and 60 sessions of history through this
app's own provider on 2026-09-06. TASE *funds* are not: an Israeli index tracker
trades under a numeric symbol (1159250.TA, 1146836.TA) and the provider returns
Not Found for every one tried. ``TA35.TA`` resolves as the INDEX, which is not
investable. So there is no shekel-denominated ETF here and the class fallbacks in
``recommendations.CLASS_ETF`` stay US-listed. That is a provider limit, in the
backlog with its un-park condition, not an omission.

TEVA, NICE and ESLT are listed here in Tel Aviv and no longer also as US ADRs:
one company should not occupy two slots in one ranking. CHKP, WIX, MNDY and GLOB
have no TASE line in this set and stay where they trade.
"""
from __future__ import annotations

from app.services import commodities as _commodities
from app.services import fixed_income as _fixed_income

# ---- US large-cap stocks (sector-diversified, not just mega-cap tech) ----
US_STOCKS: list[dict] = [
    {"ticker": "AAPL", "name": "Apple", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "MSFT", "name": "Microsoft", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "GOOGL", "name": "Alphabet", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "AMZN", "name": "Amazon", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "NVDA", "name": "NVIDIA", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "META", "name": "Meta Platforms", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "AVGO", "name": "Broadcom", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "ORCL", "name": "Oracle", "market": "NYSE", "kind": "stock"},
    {"ticker": "CRM", "name": "Salesforce", "market": "NYSE", "kind": "stock"},
    {"ticker": "ADBE", "name": "Adobe", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "AMD", "name": "Advanced Micro Devices", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "JPM", "name": "JPMorgan Chase", "market": "NYSE", "kind": "stock"},
    {"ticker": "BAC", "name": "Bank of America", "market": "NYSE", "kind": "stock"},
    {"ticker": "V", "name": "Visa", "market": "NYSE", "kind": "stock"},
    {"ticker": "MA", "name": "Mastercard", "market": "NYSE", "kind": "stock"},
    {"ticker": "BRK-B", "name": "Berkshire Hathaway", "market": "NYSE", "kind": "stock"},
    {"ticker": "UNH", "name": "UnitedHealth", "market": "NYSE", "kind": "stock"},
    {"ticker": "JNJ", "name": "Johnson & Johnson", "market": "NYSE", "kind": "stock"},
    {"ticker": "LLY", "name": "Eli Lilly", "market": "NYSE", "kind": "stock"},
    {"ticker": "PFE", "name": "Pfizer", "market": "NYSE", "kind": "stock"},
    {"ticker": "MRK", "name": "Merck", "market": "NYSE", "kind": "stock"},
    {"ticker": "ABBV", "name": "AbbVie", "market": "NYSE", "kind": "stock"},
    {"ticker": "PG", "name": "Procter & Gamble", "market": "NYSE", "kind": "stock"},
    {"ticker": "KO", "name": "Coca-Cola", "market": "NYSE", "kind": "stock"},
    {"ticker": "PEP", "name": "PepsiCo", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "COST", "name": "Costco", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "WMT", "name": "Walmart", "market": "NYSE", "kind": "stock"},
    {"ticker": "HD", "name": "Home Depot", "market": "NYSE", "kind": "stock"},
    {"ticker": "MCD", "name": "McDonald's", "market": "NYSE", "kind": "stock"},
    {"ticker": "NKE", "name": "Nike", "market": "NYSE", "kind": "stock"},
    {"ticker": "DIS", "name": "Walt Disney", "market": "NYSE", "kind": "stock"},
    {"ticker": "XOM", "name": "Exxon Mobil", "market": "NYSE", "kind": "stock"},
    {"ticker": "CVX", "name": "Chevron", "market": "NYSE", "kind": "stock"},
    {"ticker": "CAT", "name": "Caterpillar", "market": "NYSE", "kind": "stock"},
    {"ticker": "GE", "name": "GE Aerospace", "market": "NYSE", "kind": "stock"},
    {"ticker": "HON", "name": "Honeywell", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "BA", "name": "Boeing", "market": "NYSE", "kind": "stock"},
    {"ticker": "T", "name": "AT&T", "market": "NYSE", "kind": "stock"},
    {"ticker": "VZ", "name": "Verizon", "market": "NYSE", "kind": "stock"},
    {"ticker": "NEE", "name": "NextEra Energy", "market": "NYSE", "kind": "stock"},
]

# ---- Broad & sector ETFs ----
ETFS: list[dict] = [
    {"ticker": "VTI", "name": "Total US Market", "market": "NYSE", "kind": "etf"},
    {"ticker": "VOO", "name": "S&P 500", "market": "NYSE", "kind": "etf"},
    {"ticker": "QQQ", "name": "Nasdaq-100", "market": "NASDAQ", "kind": "etf"},
    {"ticker": "SCHD", "name": "US Dividend Equity", "market": "NYSE", "kind": "etf"},
    {"ticker": "VYM", "name": "High Dividend Yield", "market": "NYSE", "kind": "etf"},
    {"ticker": "VXUS", "name": "Total International", "market": "NASDAQ", "kind": "etf"},
    {"ticker": "SMH", "name": "Semiconductors", "market": "NASDAQ", "kind": "etf"},
    {"ticker": "XLE", "name": "Energy Sector", "market": "NYSE", "kind": "etf"},
    {"ticker": "XLF", "name": "Financials Sector", "market": "NYSE", "kind": "etf"},
    {"ticker": "XLV", "name": "Healthcare Sector", "market": "NYSE", "kind": "etf"},
    {"ticker": "VNQ", "name": "US Real Estate", "market": "NYSE", "kind": "etf"},
]

# ---- Tel-Aviv (TASE) listings -- priced in ILS, quoted by the provider in
# ---- agorot ("ILA"), normalised at the boundary by `fx.to_major_units`.
#
# Sector spread is deliberate: a ranking over six banks is a sector bet wearing a
# country's name. Verified live 2026-09-06 through this app's own provider --
# every entry returned sector, P/E and 60 sessions of history.
TASE_STOCKS: list[dict] = [
    {"ticker": "LUMI.TA", "name": "Bank Leumi", "market": "TASE", "kind": "stock"},
    {"ticker": "POLI.TA", "name": "Bank Hapoalim", "market": "TASE", "kind": "stock"},
    {"ticker": "MZTF.TA", "name": "Mizrahi Tefahot Bank", "market": "TASE", "kind": "stock"},
    {"ticker": "DSCT.TA", "name": "Israel Discount Bank", "market": "TASE", "kind": "stock"},
    {"ticker": "PHOE.TA", "name": "Phoenix Financial", "market": "TASE", "kind": "stock"},
    {"ticker": "HARL.TA", "name": "Harel Insurance", "market": "TASE", "kind": "stock"},
    {"ticker": "ICL.TA", "name": "ICL Group", "market": "TASE", "kind": "stock"},
    {"ticker": "TSEM.TA", "name": "Tower Semiconductor", "market": "TASE", "kind": "stock"},
    {"ticker": "NVMI.TA", "name": "Nova", "market": "TASE", "kind": "stock"},
    {"ticker": "CAMT.TA", "name": "Camtek", "market": "TASE", "kind": "stock"},
    {"ticker": "NICE.TA", "name": "NICE Ltd", "market": "TASE", "kind": "stock"},
    {"ticker": "ESLT.TA", "name": "Elbit Systems", "market": "TASE", "kind": "stock"},
    {"ticker": "TEVA.TA", "name": "Teva Pharmaceutical", "market": "TASE", "kind": "stock"},
    {"ticker": "BEZQ.TA", "name": "Bezeq", "market": "TASE", "kind": "stock"},
    {"ticker": "AZRG.TA", "name": "Azrieli Group", "market": "TASE", "kind": "stock"},
    {"ticker": "ORA.TA", "name": "Ormat Technologies", "market": "TASE", "kind": "stock"},
    {"ticker": "ELAL.TA", "name": "El Al Israel Airlines", "market": "TASE", "kind": "stock"},
]

# ---- Israeli companies listed in the US (ADRs), kept as themselves ----
#
# These are a different instrument from the Tel Aviv line above: a different
# currency, a different tax treatment, and for a shekel book an unhedged USD
# exposure. The three whose TASE listing is now in this set -- TEVA, NICE and
# ESLT -- were removed from here rather than duplicated, because one company
# holding two slots in one ranking is a thumb on the scale.
US_LISTED_ISRAELI: list[dict] = [
    {"ticker": "CHKP", "name": "Check Point Software", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "WIX", "name": "Wix.com", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "MNDY", "name": "Monday.com", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "GLOB", "name": "Globant", "market": "NYSE", "kind": "stock"},
]


def _asset_class(kind: str) -> str:
    return {"stock": "Equities", "etf": "Equities", "commodity": "Commodities",
            "bond": "Fixed Income"}[kind]


def commodity_candidates() -> list[dict]:
    out = []
    for c in _commodities.CATALOG:
        out.append({"ticker": c["ticker"], "name": c["name"], "market": "NYSE",
                    "kind": "commodity", "asset_class": "Commodities",
                    "expense_ratio_pct": c.get("expense_ratio_pct"),
                    "category": c.get("category")})
    return out


def bond_candidates() -> list[dict]:
    """The Fixed Income half of the universe, which did not exist until PR 10.

    `full_universe()` returned 64 Equities, 19 Commodities and ZERO bonds, on an
    app whose objective presets have wanted 10-60% Fixed Income since the
    beginning. Every book therefore carried a bond target it could not place:
    `_redeploy_cash_recs` looked for a Fixed Income candidate, found none, and
    recorded the money as `unplaced` on every single scan.
    """
    out = []
    for c in _fixed_income.CATALOG:
        out.append({"ticker": c["ticker"], "name": c["name"], "market": "NASDAQ",
                    "kind": "bond", "asset_class": "Fixed Income",
                    "expense_ratio_pct": c.get("expense_ratio_pct"),
                    "duration_yrs": c.get("duration_yrs"),
                    "credit": c.get("credit"), "caveat": c.get("caveat"),
                    "category": c.get("category")})
    return out


def full_universe(include_commodities: bool = True, include_bonds: bool = True) -> list[dict]:
    """The complete candidate list with asset_class filled in (deduped by ticker)."""
    rows: list[dict] = []
    for grp in (US_STOCKS, ETFS, TASE_STOCKS, US_LISTED_ISRAELI):
        for r in grp:
            rows.append({**r, "asset_class": _asset_class(r["kind"])})
    if include_commodities:
        rows += commodity_candidates()
    if include_bonds:
        rows += bond_candidates()
    seen, deduped = set(), []
    for r in rows:
        if r["ticker"] in seen:
            continue
        seen.add(r["ticker"])
        deduped.append(r)
    return deduped


def equity_universe() -> list[dict]:
    return [r for r in full_universe(include_commodities=False, include_bonds=False)]
