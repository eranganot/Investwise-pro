"""Risk assessment result (Section 4.4 output)."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.validation import FiniteFloat, NonNegFloat, STRICT, UnitFraction


class RiskAssessment(BaseModel):
    """Monte Carlo stress output. Probabilities/drawdowns are fractions in [0,1];
    returns may be negative; counts and horizons are positive and finite."""
    model_config = STRICT
    runs: int = Field(gt=0)                   # Monte Carlo paths simulated
    horizon_years: NonNegFloat
    expected_return: FiniteFloat              # annual mu (decimal, may be < 0)
    volatility: NonNegFloat                   # annual sigma (decimal)
    probability_of_ruin: UnitFraction         # P(path max drawdown >= max_drawdown_cap)
    median_max_drawdown: UnitFraction         # p50 of per-path max drawdown
    worst_case_drawdown_p95: UnitFraction     # p95 of per-path max drawdown
    expected_terminal_return: FiniteFloat     # mean terminal return across paths (may be < 0)
    terminal_return_volatility: NonNegFloat   # std of terminal returns
    distribution: str = "normal"
    assumptions: list[str] = []
