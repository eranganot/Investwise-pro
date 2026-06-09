"""Translate read-only research events into portfolio-specific impact + actions.

The Research Agent emits market events (a CPI print, an FX move, a surtax
update...). This module cross-references each event against the user's actual
holdings so the UI can say *which* of your positions it touches, *how much* is
exposed, and *what to do* — keeping everything anchored to the plan.
"""
from __future__ import annotations

from app.services.allocation_mix import classify
from app.schemas.state_machine import MARKET_CURRENCY


def _tokens(affected: list[str]) -> tuple[set[str], set[str], set[str]]:
    """Split affected_assets into (tickers, markets, currencies)."""
    tickers: set[str] = set()
    markets: set[str] = set()
    currencies: set[str] = set()
    for a in affected or []:
        a = (a or "").upper().strip()
        if "/" in a:                       # FX pair e.g. ILS/USD
            currencies.update(p.strip() for p in a.split("/") if p.strip())
        elif ":" in a:                     # VENUE:TICKER e.g. TASE:TA35
            mk, tk = a.split(":", 1)
            markets.add(mk.strip())
            tickers.add(tk.strip())
        else:
            tickers.add(a)
    return tickers, markets, currencies


# event_type keyword -> (effect template, generic actions)
def _playbook(event_type: str, holdings_txt: str, exposure_pct: int):
    et = (event_type or "").upper()
    if any(k in et for k in ("SURTAX", "TAX", "REGULAT")):
        return (
            f"Could raise the tax you owe on gains in {holdings_txt}.",
            ["Review tax-loss harvesting to offset realized gains",
             "Check whether holding via a different entity (e.g. Corp) lowers the rate",
             "Avoid realizing large gains until the rule is confirmed"],
        )
    if any(k in et for k in ("CPI", "INFLATION")):
        return (
            f"Higher inflation pressures bonds and long-duration assets in {holdings_txt}; real assets tend to hold up better.",
            ["Trim long-duration fixed income",
             "Keep or raise commodity / inflation-protected exposure"],
        )
    if any(k in et for k in ("RATE", "INTEREST", "YIELD")):
        return (
            f"Rising rates push bond prices down and weigh on rate-sensitive names in {holdings_txt}.",
            ["Shorten bond duration",
             "Re-check that rate-sensitive equities still fit your plan"],
        )
    if any(k in et for k in ("FX", "CURRENCY", "FOREX")):
        return (
            f"A currency swing changes the shekel value of your foreign holdings in {holdings_txt}.",
            ["Consider hedging the currency or rebalancing toward ILS assets",
             "Don't react to a single day's move"],
        )
    if "EARNING" in et:
        return (
            f"Expect larger price swings around the report for {holdings_txt}.",
            ["Avoid chasing the move before the print",
             "Wait for results before adding"],
        )
    return (f"Could move {holdings_txt}.", ["Watch for follow-through before acting"])


def annotate(events, rows) -> list[dict]:
    """events: list[ResearchEvent]; rows: ORM Position objects."""
    holdings = []
    nav = 0.0
    for p in rows:
        val = float(p.quantity) * float(p.current_price or 0)
        nav += val
        cls = classify(p.ticker, p.market, (p.meta or {}).get("asset_class"))
        holdings.append({
            "ticker": (p.ticker or "").upper(), "market": (p.market or "").upper(),
            "cur": MARKET_CURRENCY.get((p.market or "").upper(), "USD"),
            "cls": cls, "val": val,
        })

    out = []
    for e in events:
        d = e.model_dump()
        tickers, markets, currencies = _tokens(d.get("affected_assets", []))
        hit = []
        for h in holdings:
            if (h["ticker"] in tickers or h["market"] in markets
                    or (h["cur"] in currencies and h["cur"] != "ILS")
                    or (currencies and h["cur"] != "ILS" and "USD" in currencies)):
                hit.append(h)
        exposure = sum(h["val"] for h in hit)
        exposure_pct = round(exposure / nav * 100) if nav else 0
        names = [h["ticker"] for h in hit]
        if hit:
            holdings_txt = ", ".join(dict.fromkeys(names))
            impact, actions = _playbook(d.get("event_type", ""), holdings_txt, exposure_pct)
            direction = "watch"
        else:
            impact = "No direct overlap with your holdings — informational."
            actions = []
            direction = "info"
        d.update({
            "affected_holdings": list(dict.fromkeys(names)),
            "exposure_ils": round(exposure),
            "exposure_pct": exposure_pct,
            "impact": impact,
            "actions": actions,
            "direction": direction,
        })
        out.append(d)
    return out
