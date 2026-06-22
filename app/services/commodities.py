"""Commodity investment options - real, live-priced, retail-investable instruments.

We use liquid ETFs (clean USD prices, no futures account needed). Untradeable
"commodities" people ask about (salt, potatoes) have no financial market; we say
so and point to the closest real exposure (the broad agriculture basket).
"""
from __future__ import annotations

# ticker, display name, category, annual expense ratio %
CATALOG: list[dict] = [
    {"ticker": "GLD", "name": "Gold", "category": "Precious Metals", "expense_ratio_pct": 0.40},
    {"ticker": "IAU", "name": "Gold (low-fee)", "category": "Precious Metals", "expense_ratio_pct": 0.25},
    {"ticker": "SLV", "name": "Silver", "category": "Precious Metals", "expense_ratio_pct": 0.50},
    {"ticker": "SIVR", "name": "Silver (low-fee)", "category": "Precious Metals", "expense_ratio_pct": 0.30},
    {"ticker": "PPLT", "name": "Platinum", "category": "Precious Metals", "expense_ratio_pct": 0.60},
    {"ticker": "PALL", "name": "Palladium", "category": "Precious Metals", "expense_ratio_pct": 0.60},
    {"ticker": "GLTR", "name": "Precious metals basket", "category": "Precious Metals", "expense_ratio_pct": 0.60},
    {"ticker": "GDX", "name": "Gold miners", "category": "Precious Metals", "expense_ratio_pct": 0.51},
    {"ticker": "CPER", "name": "Copper", "category": "Industrial Metals", "expense_ratio_pct": 0.88},
    {"ticker": "JJN", "name": "Nickel", "category": "Industrial Metals", "expense_ratio_pct": 0.45},
    {"ticker": "LIT", "name": "Lithium & battery", "category": "Industrial Metals", "expense_ratio_pct": 0.75},
    {"ticker": "URA", "name": "Uranium miners", "category": "Industrial Metals", "expense_ratio_pct": 0.69},
    {"ticker": "DBB", "name": "Base metals basket", "category": "Industrial Metals", "expense_ratio_pct": 0.75},
    {"ticker": "CORN", "name": "Corn", "category": "Agriculture", "expense_ratio_pct": 1.00},
    {"ticker": "WEAT", "name": "Wheat", "category": "Agriculture", "expense_ratio_pct": 1.00},
    {"ticker": "SOYB", "name": "Soybeans", "category": "Agriculture", "expense_ratio_pct": 1.00},
    {"ticker": "CANE", "name": "Sugar", "category": "Agriculture", "expense_ratio_pct": 1.00},
    {"ticker": "JO", "name": "Coffee", "category": "Agriculture", "expense_ratio_pct": 0.45},
    {"ticker": "NIB", "name": "Cocoa", "category": "Agriculture", "expense_ratio_pct": 0.45},
    {"ticker": "COW", "name": "Livestock", "category": "Agriculture", "expense_ratio_pct": 0.45},
    {"ticker": "DBA", "name": "Agriculture basket", "category": "Agriculture", "expense_ratio_pct": 0.93},
    {"ticker": "USO", "name": "Crude oil", "category": "Energy", "expense_ratio_pct": 0.60},
    {"ticker": "BNO", "name": "Brent crude oil", "category": "Energy", "expense_ratio_pct": 1.00},
    {"ticker": "UNG", "name": "Natural gas", "category": "Energy", "expense_ratio_pct": 1.06},
    {"ticker": "UGA", "name": "Gasoline", "category": "Energy", "expense_ratio_pct": 0.96},
    {"ticker": "PDBC", "name": "Broad commodities", "category": "Diversified", "expense_ratio_pct": 0.59},
    {"ticker": "DBC", "name": "Broad commodities (DBC)", "category": "Diversified", "expense_ratio_pct": 0.85},
    {"ticker": "GSG", "name": "Broad commodities (GSG)", "category": "Diversified", "expense_ratio_pct": 0.75},
    {"ticker": "COMT", "name": "Broad commodities (COMT)", "category": "Diversified", "expense_ratio_pct": 0.48},
]

CATEGORY_ORDER = ["Precious Metals", "Industrial Metals", "Agriculture", "Energy", "Diversified"]
_BY_TICKER = {c["ticker"]: c for c in CATALOG}

# things people ask for that have no tradable market -> honest redirect
NOT_INVESTABLE = {
    "salt": "Salt has no financial market - the closest real exposure is the broad agriculture / commodities basket.",
    "potato": "Potato futures were delisted decades ago - the closest real exposure is the agriculture basket (DBA).",
    "potatoes": "Potato futures were delisted decades ago - the closest real exposure is the agriculture basket (DBA).",
    "water": "There is no simple retail water-commodity ETF; agriculture/infrastructure funds are the nearest proxy.",
}


def by_category() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {c: [] for c in CATEGORY_ORDER}
    for c in CATALOG:
        out.setdefault(c["category"], []).append(c)
    return out


def get(ticker: str) -> dict | None:
    return _BY_TICKER.get(ticker.upper())


def is_commodity(ticker: str) -> bool:
    return ticker.upper() in _BY_TICKER
