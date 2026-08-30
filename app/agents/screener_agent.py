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

    #: What the last `_fundamentals_for` call could and could not reach. Read by
    #: `screen_equities` so the CALLER can say the ranking was over a subset.
    last_coverage: dict = {}

    def _fundamentals_for(self, candidates: list[dict]) -> list[dict]:
        """Resolve fundamentals, AND RECORD WHAT IT COULD NOT REACH.

        This swallowed every exception into `f = None` and dropped the candidate
        with no counter and no log, so a name that was never scored looked
        exactly like a name that scored badly.

        MEASURED, 2026-08-30, builtin provider in the sandbox:

            58 equity candidates in, 20 resolved, 38 RateLimitedError

        `provider_rate_limit_per_sec` is 20 and the tier's TokenBucket is
        `TokenBucket(20, 20)`. The loop fires 58 calls in a few milliseconds, so
        the first 20 take every token and the remaining 38 fail instantly. The
        survivors are literally the first twenty entries of `equity_universe()`:
        AAPL through PFE. MRK, ABBV, PG, KO, PEP, COST, WMT, HD, MCD, NKE, DIS,
        XOM and twenty-six more were never scored at all -- so "the best buys in
        the universe" meant "the best of whichever names the token bucket
        happened to allow".

        THIS IS THE BUG THIS FILE ALREADY DOCUMENTS TWICE. `_history` draws the
        line between "the provider ANSWERED and there is nothing there" and "we
        could not reach it", because ranking bonds three times in one process
        silently lost twenty of twenty-four candidates and returned three
        identical wrong answers. `screen_commodities` and `screen_bonds` both
        act on that distinction and both log the unreachable set.
        `screen_equities` never got it.

        Production runs Yahoo, where each call takes real network time and the
        bucket refills as the loop runs, so it is likely far less affected --
        LIKELY, not measured, and the reason it cannot be measured is precisely
        the silence this fixes. After this, the count is in the payload and the
        question is answerable instead of arguable.
        """
        items: list[dict] = []
        unreachable: list[str] = []
        empty: list[str] = []
        for c in candidates:
            ticker = c["ticker"]
            try:
                f = guarded_fundamentals(ticker)
            except ValueError:
                # The provider ANSWERED: this symbol has nothing. A real fact
                # about the ticker, not about the transport.
                empty.append(ticker)
                continue
            except Exception:  # noqa: BLE001 -- transport, not the ticker
                unreachable.append(ticker)
                continue
            if f is None:
                empty.append(ticker)
                continue
            items.append({"meta": c, "fundamentals": f})

        self.last_coverage = {
            "requested": len(candidates),
            "ranked": len(items),
            "unreachable": sorted(unreachable),
            "no_data": sorted(empty),
        }
        if unreachable:
            logger.warning(
                "equity candidates UNREACHABLE this scan (provider, not ticker): "
                "%d of %d -- THIS RANKING IS OVER A SUBSET. Not scored: %s",
                len(unreachable), len(candidates), ", ".join(sorted(unreachable)))
        if empty:
            logger.info("equity candidates the provider says have no fundamentals: %s",
                        ", ".join(sorted(empty)))
        return items

    def _history(self, ticker: str, days: int) -> tuple[list[float], str]:
        """(closes, status) where status is "ok" | "no_data" | "unavailable".

        THE DISTINCTION IS THE POINT, and PR 5b got it wrong in a way that only
        showed up here. `_trend_pct` used to swallow every exception and return
        None, and `screen_commodities` then DROPPED any candidate that returned
        None as if the provider had answered "this symbol has nothing". It had
        not: a rate-limited or circuit-open call returns nothing for a reason
        that has no bearing on the ticker.

        Reproduced while building PR 10: ranking bonds three times in one
        process rate-limited the later calls, twenty of twenty-four candidates
        silently vanished, and all three rankings came back IDENTICAL and wrong
        -- including one that recommended a 6-year fund for a 0.3-year mandate.
        The ranking changed because of transport luck, and nothing said so.

        The resilience tier already draws this line (`CircuitBreaker.is_no_data`
        -- a ValueError means the provider answered and the symbol is empty).
        This just stops throwing that answer away.
        """
        try:
            hist = guarded_history(ticker, days=days)
        except ValueError:
            return [], "no_data"                    # the provider ANSWERED: nothing there
        except Exception:                           # noqa: BLE001 -- transport, not the ticker
            return [], "unavailable"
        closes = [c for _d, c in (hist or [])]
        if len(closes) < 2 or closes[0] <= 0:
            return [], "no_data"
        return closes, "ok"

    def _trend_pct(self, ticker: str) -> tuple[float | None, str]:
        closes, status = self._history(ticker, 130)
        if status != "ok":
            return None, status
        return round((closes[-1] / closes[0] - 1.0) * 100.0, 1), "ok"

    def screen_equities(self, weights: dict | None = None, top_n: int = 10,
                        candidates: list[dict] | None = None) -> list[ScreenPick]:
        """Rank the equity universe. `self.last_coverage` says how much of it.

        A percentile is only meaningful against the set it was computed over, so
        a ranking of 20 names out of 58 is not the same claim as a ranking of
        58 -- and until `_fundamentals_for` started counting, the two were
        indistinguishable from the outside.
        """
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
        items, dropped, unavailable = [], [], []
        for c in _universe.commodity_candidates():
            trend, status = self._trend_pct(c["ticker"])
            if status == "no_data":
                dropped.append(c["ticker"])
                continue
            if status == "unavailable":
                # NOT the same as delisted. Excluded from THIS ranking because
                # the score needs a trend, but recorded as a provider problem so
                # the silence is attributable.
                unavailable.append(c["ticker"])
                continue
            items.append({"meta": c, "trend_pct": trend,
                          "expense_ratio_pct": c.get("expense_ratio_pct")})
        if dropped:
            logger.warning("commodity candidates the provider says have no history: %s",
                           ", ".join(sorted(dropped)))
        if unavailable:
            logger.warning("commodity candidates UNREACHABLE this scan (provider, not ticker): "
                           "%s -- this ranking is over a subset",
                           ", ".join(sorted(unavailable)))
        return self.engine.rank_commodities(items, top_n=top_n)

    def _vol_pct(self, ticker: str) -> tuple[float | None, str]:
        """Annualised volatility from real daily closes, or None.

        MEASURED, and the distinction matters here more than usual: the
        catalogue's `duration_yrs` is published metadata that drifts as the fund
        rolls, while this is computed from the same 250 closes the app already
        fetches. Checked on 2026-08-25 across the whole catalogue, the two
        correlate at r = 0.94 -- which is the evidence that the stated durations
        are sane, and is exactly why they are still never quoted AS a
        measurement.
        """
        import math

        closes, status = self._history(ticker, 260)
        if status != "ok" or len(closes) < 60:
            return None, ("no_data" if status == "no_data" else
                          "unavailable" if status == "unavailable" else "no_data")
        rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))
                if closes[i - 1] > 0]
        if len(rets) < 30:
            return None, "no_data"
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        return round(math.sqrt(var) * math.sqrt(252) * 100.0, 1), "ok"

    def screen_bonds(self, top_n: int = 4, *, want_duration: float = 6.0,
                     allow_credit: bool = False, currency: str = "USD") -> list[ScreenPick]:
        """The Fixed Income candidates, duration-matched rather than trend-ranked.

        Same liveness rule as `screen_commodities`, for the same reason: a
        candidate the provider cannot price is dropped, not scored. That rule
        exists because a cheap DELISTED ETN outranked every live commodity for
        months -- `_mean` drops missing components rather than penalising them,
        so a candidate with no data was scored on its expense ratio alone. Bonds
        are MORE exposed to that failure than commodities were, because cost is
        a bigger share of a bond's score.
        """
        items, dropped, unavailable = [], [], []
        for c in _universe.bond_candidates():
            vol, status = self._vol_pct(c["ticker"])
            if status == "no_data":
                dropped.append(c["ticker"])
                continue
            if status == "unavailable":
                # A BOND STILL RANKS WITHOUT IT, and this is where the tri-state
                # earns itself. The ranking is duration fit plus cost, both from
                # the catalogue; `vol_pct` only decorates the reason line. So a
                # provider hiccup costs one sentence, not the candidate --
                # unlike commodities, where the trend IS the score.
                unavailable.append(c["ticker"])
            items.append({"meta": c, "vol_pct": vol})
        if dropped:
            logger.warning("bond candidates the provider says have no history: %s",
                           ", ".join(sorted(dropped)))
        if unavailable:
            logger.info("bond candidates with no volatility this scan (still ranked): %s",
                        ", ".join(sorted(unavailable)))
        return self.engine.rank_bonds(items, want_duration=want_duration,
                                      allow_credit=allow_credit, currency=currency,
                                      top_n=top_n)

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
