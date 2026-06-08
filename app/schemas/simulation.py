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
