"""Section AC - OPPORTUNITY AGENT (active idea-generation specialist).

Where the Research Agent only emits passive market events, the Opportunity Agent
answers a concrete question: *of everything I could buy, what is most attractive
on fundamentals right now?* It pulls the candidate universe, fetches each name's
fundamentals (guarded by the provider resilience tier - so a flaky/None response
just drops that name), scores them with the ScreenerEngine, and returns the top
buy ideas with plain-English reasons. It never mutates portfolio state.
"""
from __future__ import annotations

import logging

from app.engines.screener_engine import DEFAULT_WEIGHTS, ScreenerEngine
from app.providers.registry import guarded_fundamentals, guarded_history
from app.schemas.screener import ScreenPick
from app.services import universe as _universe

logger = logging.getLogger(__name__)


class OpportunityAgent:
    def __init__(self, engine: ScreenerEngine | None = None) -> None:
        self.engine = engine or ScreenerEngine()

    def _fundamentals_for(self, candidates: list[dict]) -> list[dict]:
        items = []
        for c in candidates:
            try:
                f = guarded_fundamentals(c["ticker"])
            except Exception:
                f = None
            if f is None:
                continue
            items.append({"meta": c, "fundamentals": f})
        return items

    def _trend_pct(self, ticker: str) -> float | None:
        try:
            hist = guarded_history(ticker, days=130)
        except Exception:
            return None
        closes = [c for _d, c in hist] if hist else []
        if len(closes) < 2 or closes[0] <= 0:
            return None
        return round((closes[-1] / closes[0] - 1.0) * 100.0, 1)

    def screen_equities(self, weights: dict | None = None, top_n: int = 10,
                        candidates: list[dict] | None = None) -> list[ScreenPick]:
        cands = candidates if candidates is not None else _universe.equity_universe()
        items = self._fundamentals_for(cands)
        if not items:
            return []
        return self.engine.rank_equities(items, weights=weights, top_n=top_n)

    def screen_commodities(self, top_n: int = 6) -> list[ScreenPick]:
        """No measured trend, no recommendation.

        A commodity is ranked 2:1 on trend against cost, and `_trend_pct`
        returns None when the provider has no history. `_mean` then DROPS the
        missing components rather than penalising them, so such a candidate is
        scored on its expense ratio alone -- and a cheap delisted ETN outranked
        every live commodity that happened to be falling. That is how COW, dead
        since 2023, won the buy card every scan for months.

        Removing the four known-dead tickers from the catalogue fixes today.
        This fixes the NEXT one to die, without waiting for someone to notice:
        a candidate the provider cannot price is not a candidate. It is dropped
        rather than scored, because a score computed from the factors that
        happen to be present is not comparable to one computed from all of them.

        The general version of this -- every card, every asset class, checked
        against a recent close -- is the backlog item in STATUS.md.
        """
        items = []
        dropped = []
        for c in _universe.commodity_candidates():
            trend = self._trend_pct(c["ticker"])
            if trend is None:
                dropped.append(c["ticker"])
                continue
            items.append({"meta": c, "trend_pct": trend,
                          "expense_ratio_pct": c.get("expense_ratio_pct")})
        if dropped:
            logger.warning("commodity candidates with no price history, not ranked: %s",
                           ", ".join(sorted(dropped)))
        return self.engine.rank_commodities(items, top_n=top_n)

    def top_ideas(self, weights: dict | None = None, n_equities: int = 8,
                  n_commodities: int = 4) -> dict:
        """The headline call: best fundamentals-ranked buys + best commodities."""
        eq = self.screen_equities(weights=weights, top_n=n_equities)
        com = self.screen_commodities(top_n=n_commodities)
        return {
            "equities": [p.model_dump() for p in eq],
            "commodities": [p.model_dump() for p in com],
            "weights": {**DEFAULT_WEIGHTS, **(weights or {})},
        }
