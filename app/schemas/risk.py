"""Risk assessment result (Section 4.4 output)."""
from __future__ import annotations

from pydantic import BaseModel


class RiskAssessment(BaseModel):
    runs: int                          # Monte Carlo paths simulated
    horizon_years: float
    expected_return: float             # annual mu (decimal)
    volatility: float                  # annual sigma (decimal)
    probability_of_ruin: float         # P(path max drawdown >= max_drawdown_cap)
    median_max_drawdown: float         # p50 of per-path max drawdown
    worst_case_drawdown_p95: float     # p95 of per-path max drawdown
    expected_terminal_return: float    # mean terminal return across paths
    terminal_return_volatility: float  # std of terminal returns
    distribution: str = "normal"
    assumptions: list[str] = []
