"""Screener / fundamentals DTOs (Section AC - Opportunity discovery).

`Fundamentals` is the vendor-neutral fundamentals payload a MarketDataProvider
can emit for a single instrument. `ScreenPick` is the engine's scored output for
one candidate. Both are intentionally provider-agnostic so the Yahoo, builtin or
any future adapter fills the same contract.
"""
from __future__ import annotations

from pydantic import BaseModel


class Fundamentals(BaseModel):
    ticker: str
    name: str = ""
    sector: str = "Unknown"
    # valuation
    pe: float | None = None              # trailing price / earnings
    pb: float | None = None              # price / book
    # growth (year-over-year, %)
    earnings_growth_pct: float | None = None
    revenue_growth_pct: float | None = None
    # quality
    profit_margin_pct: float | None = None
    roe_pct: float | None = None
    debt_to_equity: float | None = None  # %, lower is healthier
    # income
    dividend_yield_pct: float | None = None
    as_of: str = ""


class FactorScores(BaseModel):
    value: float = 0.0
    growth: float = 0.0
    quality: float = 0.0
    income: float = 0.0


class ScreenPick(BaseModel):
    ticker: str
    name: str
    market: str
    kind: str                    # stock | etf | commodity
    asset_class: str
    sector: str = "Unknown"
    score: float                 # 0-100 composite
    factor_scores: FactorScores
    metrics: dict                # the raw numbers behind the score
    reasons: list[str]           # plain-English "why it screens well"
    flags: list[str] = []        # e.g. "hype-priced", "loss-making", "partial-data"
    # Which of the eight factors did NOT contribute to `score`, and why:
    # "not reported" (the provider had nothing) or "implausible" (it had
    # something that cannot be true). A composite over five factors is a
    # different claim from one over eight; without this the two render
    # identically. Defaulted so every existing construction still validates.
    data_gaps: list[str] = []
