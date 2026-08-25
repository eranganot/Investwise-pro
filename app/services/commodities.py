"""Commodity investment options - real, live-priced, retail-investable instruments.

We use liquid ETFs (clean USD prices, no futures account needed). Untradeable
"commodities" people ask about (salt, potatoes) have no financial market; we say
so and point to the closest real exposure (the broad agriculture basket).

"LIVE-PRICED" IS A PROMISE THIS FILE MADE AND DID NOT KEEP. Four delisted iPath
ETNs sat in the catalogue being recommended for months -- see DELISTED below.
Nothing verifies that an entry here still trades, and nothing anywhere stops a
card naming a ticker with no recent close. That gate is a backlog item
(STATUS.md, "a card may not name a ticker that is not trading"); until it
exists, this catalogue is hand-maintained and DELISTED is the record of why.
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
    {"ticker": "LIT", "name": "Lithium & battery", "category": "Industrial Metals", "expense_ratio_pct": 0.75},
    {"ticker": "URA", "name": "Uranium miners", "category": "Industrial Metals", "expense_ratio_pct": 0.69},
    {"ticker": "DBB", "name": "Base metals basket", "category": "Industrial Metals", "expense_ratio_pct": 0.75},
    {"ticker": "CORN", "name": "Corn", "category": "Agriculture", "expense_ratio_pct": 1.00},
    {"ticker": "WEAT", "name": "Wheat", "category": "Agriculture", "expense_ratio_pct": 1.00},
    {"ticker": "SOYB", "name": "Soybeans", "category": "Agriculture", "expense_ratio_pct": 1.00},
    {"ticker": "CANE", "name": "Sugar", "category": "Agriculture", "expense_ratio_pct": 1.00},
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

# DELISTED, and this list is the reason they cannot quietly come back.
#
# All four are iPath ETNs Barclays has redeemed. Yahoo answers for every one of
# them with an empty series -- not an error, just nothing -- and the app went on
# recommending them for months. Eran: "I'm always getting the COW recommendation,
# though the COW price is irrelevant and not updating for more than 6 months."
#
# He was seeing a real defect and it was not a coincidence that COW won every
# time. `rank_commodities` scores 2:1 trend-to-cost, `_trend_pct` returns None
# when there is no history, and `_mean` DROPS the Nones instead of penalising
# them -- so a ticker with no price data is scored on its expense ratio alone.
# COW's 0.45% is among the cheapest in the catalogue, so a dead ETN beat every
# live commodity that happened to be falling, every single scan. Absence of
# evidence was scoring as strength.
#
# Checked against Yahoo on 2026-08-25: the other 25 all returned 251 daily bars
# ending on the previous close. These four returned none.
DELISTED = {
    "JJN": "iPath Nickel ETN -- redeemed; no prices since 2023",
    "JO": "iPath Coffee ETN -- redeemed; no prices since 2023",
    "NIB": "iPath Cocoa ETN -- redeemed; no prices since 2023",
    "COW": "iPath Livestock ETN -- redeemed; no prices since 2023",
}

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
