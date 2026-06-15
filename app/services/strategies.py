"""Investment management strategies (Plan page).

Each top-level GOAL (Grow / Balanced / Income / Preserve) offers several concrete,
named strategies. A strategy is a full preset: it sets the objective + risk
tolerance + target asset-class allocation that drive the agents/recommendations,
and carries a real model basket of tickers you can one-click load. Baskets are
illustrative starting points, not advice.
"""
from __future__ import annotations

# goal -> list of strategies
CATALOG: list[dict] = [
    # ---------------- GROW ----------------
    {"id": "grow_ai_semis", "goal": "Grow", "name": "Aggressive AI & Semiconductors",
     "risk_tolerance": "High", "objective": "Grow", "preferred_depth": 3,
     "description": "High-risk, growth-tilted: AI leaders, chips and a small leveraged sleeve. "
                    "Highest expected return and highest drawdowns.",
     "target_allocation": {"Equities": 1.0},
     "basket": [["SMH", 0.20], ["QQQ", 0.15], ["NVDA", 0.15], ["AVGO", 0.15],
                ["GOOGL", 0.15], ["AMD", 0.12], ["TQQQ", 0.08]]},
    {"id": "grow_quality", "goal": "Grow", "name": "Quality Compounders",
     "risk_tolerance": "High", "objective": "Grow", "preferred_depth": 3,
     "description": "Concentrated in dominant, profitable growth franchises. Aggressive but "
                    "less speculative than the AI/leveraged basket.",
     "target_allocation": {"Equities": 1.0},
     "basket": [["MSFT", 0.20], ["GOOGL", 0.20], ["AMZN", 0.18], ["META", 0.15],
                ["V", 0.15], ["COST", 0.12]]},
    {"id": "grow_diversified", "goal": "Grow", "name": "Diversified Global Growth",
     "risk_tolerance": "Medium", "objective": "Grow", "preferred_depth": 2,
     "description": "Broad growth via index ETFs (US + international + tech). Growth exposure "
                    "with far less single-name risk.",
     "target_allocation": {"Equities": 1.0},
     "basket": [["VTI", 0.40], ["QQQ", 0.25], ["VXUS", 0.20], ["SMH", 0.15]]},
    {"id": "grow_leveraged", "goal": "Grow", "name": "Leveraged Momentum",
     "risk_tolerance": "High", "objective": "Grow", "preferred_depth": 1,
     "description": "Turbocharged: leveraged Nasdaq plus momentum leaders. Extreme risk - large, "
                    "fast drawdowns are expected.",
     "target_allocation": {"Equities": 1.0},
     "basket": [["TQQQ", 0.35], ["QQQ", 0.25], ["NVDA", 0.20], ["SMH", 0.20]]},
    # ---------------- BALANCED ----------------
    {"id": "bal_6040", "goal": "Balanced", "name": "Classic 60/40",
     "risk_tolerance": "Medium", "objective": "Balanced", "preferred_depth": 2,
     "description": "The time-tested 60% stocks / 40% bonds mix. Moderate growth with a bond cushion.",
     "target_allocation": {"Equities": 0.60, "Fixed Income": 0.40},
     "basket": [["VTI", 0.60], ["BND", 0.40]]},
    {"id": "bal_all_weather", "goal": "Balanced", "name": "All-Weather",
     "risk_tolerance": "Medium", "objective": "Balanced", "preferred_depth": 2,
     "description": "Diversified across stocks, bonds, gold and commodities to weather many regimes.",
     "target_allocation": {"Equities": 0.35, "Fixed Income": 0.40, "Commodities": 0.25},
     "basket": [["VTI", 0.35], ["BND", 0.40], ["IAU", 0.15], ["DBC", 0.10]]},
    {"id": "bal_commodities", "goal": "Balanced", "name": "Stocks, Bonds & Commodities",
     "risk_tolerance": "Medium", "objective": "Balanced", "preferred_depth": 2,
     "description": "A real-asset balance: equities, bonds and a 20% commodities sleeve (broad basket + gold) "
                    "as an inflation hedge and diversifier.",
     "target_allocation": {"Equities": 0.50, "Fixed Income": 0.30, "Commodities": 0.20},
     "basket": [["VTI", 0.50], ["BND", 0.30], ["DBC", 0.12], ["IAU", 0.08]]},
    {"id": "bal_7030", "goal": "Balanced", "name": "Growth-Tilted 70/30",
     "risk_tolerance": "Medium", "objective": "Balanced", "preferred_depth": 2,
     "description": "A more aggressive balance: 70% equities (with a tech tilt) / 30% bonds.",
     "target_allocation": {"Equities": 0.70, "Fixed Income": 0.30},
     "basket": [["VTI", 0.50], ["QQQ", 0.20], ["BND", 0.30]]},
    # ---------------- INCOME ----------------
    {"id": "inc_dividend_growth", "goal": "Income", "name": "Dividend Growth",
     "risk_tolerance": "Medium", "objective": "Income", "preferred_depth": 1,
     "description": "Companies that consistently grow their dividends - income plus some growth.",
     "target_allocation": {"Equities": 1.0},
     "basket": [["SCHD", 0.50], ["VIG", 0.30], ["VYM", 0.20]]},
    {"id": "inc_high_yield", "goal": "Income", "name": "High-Yield Income",
     "risk_tolerance": "Medium", "objective": "Income", "preferred_depth": 1,
     "description": "Maximizes current yield via option-income and high-yield credit. Higher risk.",
     "target_allocation": {"Equities": 0.70, "Fixed Income": 0.30},
     "basket": [["JEPI", 0.40], ["SCHD", 0.30], ["HYG", 0.30]]},
    {"id": "inc_bond_dividend", "goal": "Income", "name": "Bonds + Dividends",
     "risk_tolerance": "Low", "objective": "Income", "preferred_depth": 1,
     "description": "Bond-heavy with a dividend-equity sleeve. Steadier income, lower volatility.",
     "target_allocation": {"Fixed Income": 0.50, "Equities": 0.50},
     "basket": [["BND", 0.50], ["SCHD", 0.30], ["VYM", 0.20]]},
    # ---------------- PRESERVE ----------------
    {"id": "pre_capital", "goal": "Preserve", "name": "Capital Preservation",
     "risk_tolerance": "Low", "objective": "Preserve", "preferred_depth": 1,
     "description": "Short T-bills and short bonds. Protect principal; minimal volatility.",
     "target_allocation": {"Cash": 0.60, "Fixed Income": 0.40},
     "basket": [["BIL", 0.60], ["SHY", 0.40]]},
    {"id": "pre_inflation", "goal": "Preserve", "name": "Inflation-Protected",
     "risk_tolerance": "Low", "objective": "Preserve", "preferred_depth": 1,
     "description": "TIPS plus a gold hedge - preserve purchasing power against inflation.",
     "target_allocation": {"Fixed Income": 0.50, "Commodities": 0.25, "Cash": 0.25},
     "basket": [["TIP", 0.50], ["IAU", 0.25], ["BIL", 0.25]]},
    {"id": "pre_low_vol", "goal": "Preserve", "name": "Low-Volatility Defensive",
     "risk_tolerance": "Low", "objective": "Preserve", "preferred_depth": 1,
     "description": "Minimum-volatility equities with a large bond anchor. Gentle growth, soft ride.",
     "target_allocation": {"Equities": 0.50, "Fixed Income": 0.40, "Cash": 0.10},
     "basket": [["USMV", 0.50], ["BND", 0.40], ["BIL", 0.10]]},
]

GOAL_ORDER = ["Grow", "Balanced", "Income", "Preserve"]
_BY_ID = {s["id"]: s for s in CATALOG}

# infer an asset class for each basket ticker so loaded baskets carry it
_TICKER_CLASS = {
    "BND": "Fixed Income", "BIL": "Cash", "SHY": "Fixed Income", "TIP": "Fixed Income",
    "HYG": "Fixed Income", "IAU": "Commodities", "DBC": "Commodities", "GLD": "Commodities",
    "SLV": "Commodities", "DBA": "Commodities", "PDBC": "Commodities", "USO": "Commodities",
}


def by_goal() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {g: [] for g in GOAL_ORDER}
    for s in CATALOG:
        out.setdefault(s["goal"], []).append(s)
    return out


def get(strategy_id: str) -> dict | None:
    return _BY_ID.get(strategy_id)


def ticker_asset_class(ticker: str, strategy: dict) -> str:
    if ticker in _TICKER_CLASS:
        return _TICKER_CLASS[ticker]
    # default to the strategy's dominant class
    alloc = strategy.get("target_allocation", {})
    return max(alloc, key=alloc.get) if alloc else "Equities"
