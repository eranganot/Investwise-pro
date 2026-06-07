"""4.6 SIMULATION ENGINE - month/quarter/year horizon projections.

Inputs: CPI, FX, Spot Volatility. Phase 0 stub returns the contract shape.
"""
from __future__ import annotations

from app.core.config import Settings, get_settings


class SimulationEngine:
    HORIZONS = ("month", "quarter", "year")

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def run(self, horizon: str = "month") -> dict:
        if horizon not in self.HORIZONS:
            raise ValueError(f"horizon must be one of {self.HORIZONS}")
        return {
            "horizon": horizon,
            "inputs": {"cpi": None, "fx": None, "spot_vol": None},
            "projection": None,
            "status": "Awaiting Data - simulation not yet implemented (Phase 5).",
        }
