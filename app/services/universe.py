"""Candidate universe for the Opportunity (screener) agent.

A curated, liquid set the screener ranks across: large-cap US stocks (sector-
diversified), broad/sector ETFs, Tel-Aviv (TASE) names, plus every commodity in
the commodity catalog. Kept deliberately bounded (~70 names) so a live provider
can fetch fundamentals for the whole set within the resilience budget.

Each entry: ticker, name, market, asset_class, kind (stock | etf | commodity).
"""
from __future__ import annotations

from app.services import commodities as _commodities

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

# ---- Tel-Aviv (TASE) names (Yahoo .TA suffix) ----
TASE_STOCKS: list[dict] = [
    {"ticker": "TEVA", "name": "Teva Pharmaceutical", "market": "NYSE", "kind": "stock"},
    {"ticker": "NICE", "name": "NICE Ltd", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "CHKP", "name": "Check Point Software", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "ESLT", "name": "Elbit Systems", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "WIX", "name": "Wix.com", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "MNDY", "name": "Monday.com", "market": "NASDAQ", "kind": "stock"},
    {"ticker": "GLOB", "name": "Globant", "market": "NYSE", "kind": "stock"},
]


def _asset_class(kind: str) -> str:
    return {"stock": "Equities", "etf": "Equities", "commodity": "Commodities"}[kind]


def commodity_candidates() -> list[dict]:
    out = []
    for c in _commodities.CATALOG:
        out.append({"ticker": c["ticker"], "name": c["name"], "market": "NYSE",
                    "kind": "commodity", "asset_class": "Commodities",
                    "expense_ratio_pct": c.get("expense_ratio_pct"),
                    "category": c.get("category")})
    return out


def full_universe(include_commodities: bool = True) -> list[dict]:
    """The complete candidate list with asset_class filled in (deduped by ticker)."""
    rows: list[dict] = []
    for grp in (US_STOCKS, ETFS, TASE_STOCKS):
        for r in grp:
            rows.append({**r, "asset_class": _asset_class(r["kind"])})
    if include_commodities:
        rows += commodity_candidates()
    seen, deduped = set(), []
    for r in rows:
        if r["ticker"] in seen:
            continue
        seen.add(r["ticker"])
        deduped.append(r)
    return deduped


def equity_universe() -> list[dict]:
    return [r for r in full_universe(include_commodities=False)]
