"""Screener engine (Section AC) - turns fundamentals into a ranked buy list.

Scoring is deliberately *fundamentals-driven, not hype-driven*: it ranks names
on Value, Growth, Quality and Income relative to the rest of the candidate set
(cross-sectional percentiles) and never rewards raw price momentum. Names that
are merely expensive (very high P/E or P/B) are flagged and pushed down, so a
loud, richly-valued story does not screen well on price alone.
"""
from __future__ import annotations

import math

from app.schemas.screener import FactorScores, Fundamentals, ScreenPick

DEFAULT_WEIGHTS = {"value": 0.30, "growth": 0.25, "quality": 0.30, "income": 0.15}


def _pct_ranks(values: list[float | None], higher_is_better: bool) -> list[float | None]:
    """Map each value to a 0-100 percentile within the present (non-None) set.

    **TIES SHARE A RANK.** They did not, and the consequence was not cosmetic:
    `sorted` breaks ties by list position, so eight bond funds all charging
    0.03% were handed percentiles spread from 100 down to 70 purely by their
    order in the catalogue. Two funds identical on a factor scored differently
    on it, and moving a line in a data file changed the recommendation.

    Found while building the bond ranker (PR 10): with `want_duration=0.3`,
    SGOV -- an exact match at 0.1 years -- ranked FOURTH, behind a 2.6-year
    corporate fund, because SGOV tied BIL on duration fit and lost the tiebreak
    to list order. The ranker was right and the percentile helper was not.

    This helper is shared with `rank_equities` and `rank_commodities`, so the
    same silent list-order dependence was in every screen the app has ever run.
    """
    present = [(i, v) for i, v in enumerate(values) if v is not None]
    out: list[float | None] = [None] * len(values)
    if not present:
        return out
    if len(present) == 1:
        out[present[0][0]] = 50.0
        return out
    ordered = sorted(present, key=lambda iv: iv[1], reverse=not higher_is_better)
    # ordered[0] is the WORST -> 0, ordered[-1] is the BEST -> 100. Every run of
    # equal values takes the MEAN of the ranks it spans, so identical inputs get
    # identical outputs whatever order they arrived in.
    n = len(ordered) - 1
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        shared = round(100.0 * ((i + j) / 2.0) / n, 1)
        for k in range(i, j + 1):
            out[ordered[k][0]] = shared
        i = j + 1
    return out


# How far off the mandate a bond may sit and still be the same KIND of
# instrument. A log-ratio, so it means the same thing at every point on the
# curve: 0.5 is a factor of ~1.65, i.e. a 6-year mandate accepts roughly 3.6 to
# 10 years, and a 0.3-year mandate accepts roughly 0 to 0.65.
_BOND_DURATION_BAND = 0.5


def _mean(xs: list[float | None]) -> float:
    vals = [x for x in xs if x is not None]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


class ScreenerEngine:
    def rank_equities(self, items: list[dict], weights: dict | None = None,
                      top_n: int | None = None) -> list[ScreenPick]:
        """items: [{"meta": {...}, "fundamentals": Fundamentals}]. Returns scored picks."""
        w = {**DEFAULT_WEIGHTS, **(weights or {})}
        funds: list[Fundamentals] = [it["fundamentals"] for it in items]

        # cross-sectional percentile columns
        pe = _pct_ranks([_safe_pe(f.pe) for f in funds], higher_is_better=False)
        pb = _pct_ranks([f.pb for f in funds], higher_is_better=False)
        eg = _pct_ranks([f.earnings_growth_pct for f in funds], higher_is_better=True)
        rg = _pct_ranks([f.revenue_growth_pct for f in funds], higher_is_better=True)
        pm = _pct_ranks([f.profit_margin_pct for f in funds], higher_is_better=True)
        roe = _pct_ranks([f.roe_pct for f in funds], higher_is_better=True)
        de = _pct_ranks([f.debt_to_equity for f in funds], higher_is_better=False)
        dy = _pct_ranks([f.dividend_yield_pct for f in funds], higher_is_better=True)

        picks: list[ScreenPick] = []
        for i, it in enumerate(items):
            f = funds[i]
            meta = it["meta"]
            fs = FactorScores(
                value=_mean([pe[i], pb[i]]),
                growth=_mean([eg[i], rg[i]]),
                quality=_mean([pm[i], roe[i], de[i]]),
                income=_mean([dy[i]]),
            )
            composite = round(
                fs.value * w["value"] + fs.growth * w["growth"]
                + fs.quality * w["quality"] + fs.income * w["income"], 1)

            flags = _flags(f)
            if "loss-making" in flags:
                composite = round(composite * 0.6, 1)   # penalize unprofitable names
            if "hype-priced" in flags:
                composite = round(composite * 0.8, 1)   # penalize richly-valued names

            picks.append(ScreenPick(
                ticker=meta["ticker"], name=f.name or meta.get("name", meta["ticker"]),
                market=meta.get("market", "NYSE"), kind=meta.get("kind", "stock"),
                asset_class=meta.get("asset_class", "Equities"),
                sector=f.sector or "Unknown",
                score=max(0.0, min(100.0, composite)),
                factor_scores=fs,
                metrics={"pe": f.pe, "pb": f.pb, "earnings_growth_pct": f.earnings_growth_pct,
                         "revenue_growth_pct": f.revenue_growth_pct,
                         "profit_margin_pct": f.profit_margin_pct, "roe_pct": f.roe_pct,
                         "debt_to_equity": f.debt_to_equity,
                         "dividend_yield_pct": f.dividend_yield_pct},
                reasons=_reasons(f, fs),
                flags=flags,
            ))

        picks.sort(key=lambda p: p.score, reverse=True)
        return picks[:top_n] if top_n else picks

    def rank_commodities(self, items: list[dict], top_n: int | None = None) -> list[ScreenPick]:
        """items: [{"meta": {...}, "trend_pct": float, "expense_ratio_pct": float}].

        Commodities have no earnings, so we rank on trailing trend (momentum) and
        cost (expense ratio) - and we label them honestly as such.
        """
        trend = _pct_ranks([it.get("trend_pct") for it in items], higher_is_better=True)
        exp = _pct_ranks([it.get("expense_ratio_pct") for it in items], higher_is_better=False)
        picks: list[ScreenPick] = []
        for i, it in enumerate(items):
            meta = it["meta"]
            score = round(_mean([trend[i], trend[i], exp[i]]), 1)  # 2:1 trend:cost
            reasons = []
            tp = it.get("trend_pct")
            if tp is not None:
                reasons.append(f"{'Up' if tp >= 0 else 'Down'} {abs(tp):.0f}% over the last ~6 months")
            if it.get("expense_ratio_pct") is not None:
                reasons.append(f"Expense ratio {it['expense_ratio_pct']:.2f}%/yr")
            picks.append(ScreenPick(
                ticker=meta["ticker"], name=meta.get("name", meta["ticker"]),
                market=meta.get("market", "NYSE"), kind="commodity",
                asset_class="Commodities", sector=meta.get("category", "Commodity"),
                score=max(0.0, min(100.0, score)),
                factor_scores=FactorScores(),
                metrics={"trend_pct": tp, "expense_ratio_pct": it.get("expense_ratio_pct")},
                reasons=reasons or ["Trend/cost-ranked (no fundamentals for commodities)"],
                flags=["momentum-ranked"],
            ))
        picks.sort(key=lambda p: p.score, reverse=True)
        return picks[:top_n] if top_n else picks

    def rank_bonds(self, items: list[dict], *, want_duration: float = 6.0,
                   allow_credit: bool = False, currency: str = "USD",
                   top_n: int | None = None) -> list[ScreenPick]:
        """items: [{"meta": {...}, "vol_pct": float|None}].

        **NOT TREND-RANKED, DELIBERATELY, AND THIS IS THE WHOLE POINT.**

        `rank_commodities` scores 2:1 on trailing momentum, and copying that here
        would have been the obvious thing to do. It is precisely backwards for
        bonds. A bond fund's trailing return is almost entirely "what rates did",
        so ranking on it means buying whichever fund has the MOST duration right
        after the rate move that rewarded it -- maximum sensitivity, worst entry.
        Momentum-chasing duration is a strategy; it is not what a book filling a
        core's ballast allocation is asking for, and it must not arrive disguised
        as a neutral screen.

        What actually decides which bond fund fits:

          1. **Duration fit.** How close the fund sits to the duration the
             mandate wants. A 40%-of-core bond sleeve held against a leveraged
             equity book wants intermediate duration -- enough to rally when
             equities fall, not so much that it becomes its own rates bet.
          2. **Credit.** High yield and EM debt fall WITH equities. As ballast
             they are worse than nothing, because they look like a hedge on the
             allocation screen and behave like more equity in the drawdown. They
             are excluded unless the caller explicitly asks for credit.
          3. **Cost.** Bond returns are small, so an expense ratio is a much
             larger share of them than it is for equities. 0.03% against 0.49%
             is most of a rate cut.

        `vol_pct` is MEASURED -- annualised from real daily closes by the caller
        -- while `duration_yrs` on the catalogue is published metadata that
        drifts. Both are reported; only the measured one is called measured.
        """
        from app.services import fixed_income as _fi

        elig = [it for it in items
                if allow_credit or (it["meta"].get("credit") not in _fi.EQUITY_LIKE_CREDIT)]
        # MUNICIPALS ARE NOT AN OPTION FOR A NON-US BOOK, and they rank well on
        # every factor this function measures: VTEB charges 0.03% and sits at
        # 6.5 years, so on cost and duration it beats most Treasuries. What it
        # sells is exemption from US federal income tax, and that exemption is
        # PRICED INTO THE YIELD -- a non-US holder takes the lower yield and
        # gets nothing back for it. Ranking one first for an ILS book would be
        # a recommendation that is strictly worse than the alternative while
        # scoring better on every visible number.
        if (currency or "USD").upper() != "USD":
            elig = [it for it in elig if it["meta"].get("credit") != "municipal"]
        if not elig:
            return []

        # Distance from the wanted duration, measured PROPORTIONALLY, because
        # rate sensitivity is proportional. A linear gap gets this badly wrong
        # at the short end: 1.9 years against a 0.3-year mandate is nineteen
        # times the rate risk, but as a difference it is 1.6 -- the same 1.6
        # that is a rounding error against a 6-year mandate.
        #
        # Caught by exactly that case. With a linear miss and a floor of 1.0,
        # SGOV (0.1y, an near-exact match) lost a 0.3-year mandate to VGSH
        # (1.9y) on 6 basis points of fee. Six basis points do not buy back
        # nineteen times the duration.
        #
        # The +0.25 offset keeps a zero-duration floating-rate fund finite
        # without distorting anything at the intermediate end.
        def _miss(it) -> float:
            d = float(it["meta"].get("duration_yrs") or 0.0)
            return abs(math.log((d + 0.25) / (want_duration + 0.25)))

        # DURATION IS A GATE, NOT A WEIGHT -- and getting there took two wrong
        # answers worth recording.
        #
        # First attempt scored fit and cost as percentiles, 2:1. Percentile
        # ranks discard MAGNITUDE, and on this catalogue that is fatal: ten
        # funds charge 0.03% and one charges 0.09%, so a six-basis-point
        # difference costs 42 percentile points -- the same penalty a 46bp
        # difference would get. SGOV, a near-exact match for a cash-like
        # mandate, lost to a fund with NINETEEN TIMES its rate sensitivity, on
        # 6bp. No weighting of those two percentiles fixes that; every ratio I
        # tried was reverse-engineered from the answer I wanted.
        #
        # The structure is wrong, not the weights. A bond fund's job is to
        # deliver a duration: missing it is a RISK error, paying more is a
        # RETURN error, and for ballast the risk error is the whole point of
        # holding the thing. So duration filters, and cost ranks what survives.
        band = [it for it in elig if _miss(it) <= _BOND_DURATION_BAND]
        widened = False
        if not band:
            # Nothing close enough. Say so by widening rather than returning
            # nothing, but flag every pick -- a fund outside the band is a
            # different instrument from the one that was asked for.
            band, widened = elig, True
        exp = _pct_ranks([it["meta"].get("expense_ratio_pct") for it in band],
                         higher_is_better=False)
        # Fit still breaks ties: among ten funds all at 0.03%, the one that
        # actually matches the mandate should win.
        fit = _pct_ranks([_miss(it) for it in band], higher_is_better=False)
        picks: list[ScreenPick] = []
        for i, it in enumerate(band):
            meta = it["meta"]
            score = round(_mean([exp[i], exp[i], exp[i], fit[i]]), 1)   # cost, fit as tiebreak
            dur = meta.get("duration_yrs")
            reasons = [f"{_fi.duration_bucket(dur)} duration (~{dur:g}y) against a "
                       f"~{want_duration:g}y mandate"]
            if meta.get("expense_ratio_pct") is not None:
                reasons.append(f"Expense ratio {meta['expense_ratio_pct']:.2f}%/yr")
            if it.get("vol_pct") is not None:
                reasons.append(f"Measured volatility {it['vol_pct']:.1f}%/yr over the last year")
            if meta.get("credit") == "government":
                reasons.append("Government credit -- no default risk to correlate with equities")
            flags = ["duration-matched", "not-momentum-ranked"]
            if widened:
                flags.append("outside-mandate-band")
                reasons.append(f"No fund sat close to a ~{want_duration:g}y mandate, so this "
                               f"is the best of a wider set -- check the duration before buying.")
            if meta.get("caveat"):
                flags.append("caveat")
                reasons.append(meta["caveat"])
            picks.append(ScreenPick(
                ticker=meta["ticker"], name=meta.get("name", meta["ticker"]),
                market=meta.get("market", "NASDAQ"), kind="bond",
                asset_class="Fixed Income", sector=meta.get("category", "Bond"),
                score=max(0.0, min(100.0, score)),
                factor_scores=FactorScores(),
                metrics={"duration_yrs": dur, "expense_ratio_pct": meta.get("expense_ratio_pct"),
                         "vol_pct": it.get("vol_pct"), "credit": meta.get("credit")},
                reasons=reasons, flags=flags,
            ))
        picks.sort(key=lambda p: p.score, reverse=True)
        return picks[:top_n] if top_n else picks


def _safe_pe(pe: float | None) -> float | None:
    # A negative or zero P/E means no earnings -> treat as the worst possible value.
    if pe is None:
        return None
    return pe if pe > 0 else 9_999.0


def _flags(f: Fundamentals) -> list[str]:
    flags = []
    if (f.pe is not None and f.pe <= 0) or (f.profit_margin_pct is not None and f.profit_margin_pct < 0):
        flags.append("loss-making")
    if (f.pe is not None and f.pe > 60) or (f.pb is not None and f.pb > 10):
        flags.append("hype-priced")
    return flags


def _reasons(f: Fundamentals, fs: FactorScores) -> list[str]:
    out: list[str] = []
    if f.pe is not None and 0 < f.pe <= 18 and fs.value >= 55:
        out.append(f"Cheap on earnings (P/E {f.pe:.0f})")
    if f.pb is not None and f.pb <= 2.5 and fs.value >= 55:
        out.append(f"Trades near book value (P/B {f.pb:.1f})")
    if f.earnings_growth_pct is not None and f.earnings_growth_pct >= 15:
        out.append(f"Earnings growing ~{f.earnings_growth_pct:.0f}%/yr")
    if f.revenue_growth_pct is not None and f.revenue_growth_pct >= 12:
        out.append(f"Revenue growing ~{f.revenue_growth_pct:.0f}%/yr")
    if f.roe_pct is not None and f.roe_pct >= 18:
        out.append(f"High return on equity ({f.roe_pct:.0f}%)")
    if f.profit_margin_pct is not None and f.profit_margin_pct >= 18:
        out.append(f"Strong margins ({f.profit_margin_pct:.0f}%)")
    if f.debt_to_equity is not None and f.debt_to_equity <= 60:
        out.append("Conservative balance sheet (low debt)")
    if f.dividend_yield_pct is not None and f.dividend_yield_pct >= 2.5:
        out.append(f"Pays a {f.dividend_yield_pct:.1f}% dividend")
    return out[:4] or ["Balanced fundamentals across value, growth and quality"]
