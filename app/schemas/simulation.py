"""Simulation projection result (Section 4.6 output)."""
from __future__ import annotations

from pydantic import BaseModel


class Band(BaseModel):
    mean: float
    p5: float
    p50: float
    p95: float


class SimulationResult(BaseModel):
    horizon: str
    horizon_years: float
    runs: int
    initial_value: float
    expected_return: float   # annual decimal
    volatility: float        # annual decimal
    cpi: float               # annual decimal
    fx_change: float         # annual decimal
    nominal: Band            # projected value (currency, not inflation-adjusted)
    real: Band               # inflation-adjusted (purchasing power)
    probability_of_loss_real: float   # P(real terminal < initial value)
    probability_of_gain_real: float
    probability_meets_target: float | None = None
    distribution: str = "normal"

    # W3. All defaulted, so a caller that never asks for contributions gets a
    # payload whose every pre-W3 field means exactly what it meant before.
    contribution_per_year: float = 0.0
    contribution_total: float = 0.0
    contribution_timing: str = "end"   # deposits at period end (ordinary annuity)
    steps: int = 1                     # 1 == the single-shot path
    money_in: float = 0.0              # initial_value + contribution_total
    # P(real terminal < everything you put in). Equals
    # `probability_of_loss_real` when there are no deposits, and is the only one
    # of the two that still means "did I lose money" once there are.
    probability_of_loss_vs_money_in: float | None = None

    assumptions: list[str] = []
