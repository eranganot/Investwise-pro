"""Historical backtesting outputs (Phase 3.3)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.schemas.validation import FiniteFloat, NonNegFloat, STRICT


class EventResult(BaseModel):
    model_config = STRICT
    event: str
    label: str
    market_drawdown_pct: NonNegFloat
    portfolio_drawdown_pct: NonNegFloat
    portfolio_return_pct: FiniteFloat   # total return over the window (usually negative)


class BacktestReport(BaseModel):
    model_config = STRICT
    structural_beta: FiniteFloat                  # weighted asset-class beta of the book
    risk_implied_beta: Optional[FiniteFloat] = None  # vol-implied beta from the Risk Agent
    beta_divergence: Optional[NonNegFloat] = None
    beta_tolerance: NonNegFloat
    beta_validated: bool
    worst_event: str
    worst_portfolio_drawdown_pct: NonNegFloat
    events: list[EventResult]
    critique: str
