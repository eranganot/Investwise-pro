"""Screener engine (Section AC) - turns fundamentals into a ranked buy list.

Scoring is deliberately *fundamentals-driven, not hype-driven*: it ranks names
on Value, Growth, Quality and Income relative to the rest of the candidate set
(cross-sectional percentiles) and never rewards raw price momentum. Names that
are merely expensive (very high P/E or P/B) are flagged and pushed down, so a
loud, richly-valued story does not screen well on price alone.
"""
from __future__ import annotations

from app.schemas.screener import FactorScores, Fundamentals, ScreenPick

DEFAULT_WEIGHTS = {"value": 0.30, "growth": 0.25, "quality": 0.30, "income": 0.15}


def _pct_ranks(values: list[float | None], higher_is_better: bool) -> list[float | None]:
    """Map each value to a 0-100 percentile within the present (non-None) set."""
    present = [(i, v) for i, v in enumerate(values) if v is not None]
    out: list[float | None] = [None] * len(values)
    if not present:
        return out
    if len(present) == 1:
        out[present[0][0]] = 50.0
        return out
    ordered = sorted(present, key=lambda iv: iv[1], reverse=not higher_is_better)
    # ordered[0] is the WORST -> 0, ordered[-1] is the BEST -> 100
    n = len(ordered) - 1
    for rank, (idx, _v) in enumerate(ordered):
        out[idx] = round(100.0 * rank / n, 1)
    return out


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
