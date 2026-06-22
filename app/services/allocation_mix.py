"""Shared asset-class classification + objective targets for mix/rebalance."""
from __future__ import annotations

OBJ_TARGET = {
    "Grow": {"Equities": 0.80, "Fixed Income": 0.10, "Commodities": 0.10},
    "Balanced": {"Equities": 0.60, "Fixed Income": 0.30, "Commodities": 0.10},
    "Preserve": {"Equities": 0.30, "Fixed Income": 0.60, "Cash": 0.10},
    "Income": {"Equities": 0.40, "Fixed Income": 0.50, "Commodities": 0.10},
}


def classify(ticker: str, market: str, asset_class: str | None = None) -> str:
    if asset_class:
        return asset_class
    t = (ticker or "").upper()
    if any(k in t for k in ("BOND", "BND", "AGG", "GOV", "GILT")):
        return "Fixed Income"
    if market == "SPOT" or any(k in t for k in ("GOLD", "OIL", "SILVER")):
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
