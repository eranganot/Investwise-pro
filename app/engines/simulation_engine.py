"""4.6 SIMULATION ENGINE - forward projections (Phase 5).

Monte Carlo lognormal projection of portfolio value over a user-selected
horizon (month / quarter / year). Inputs: expected return, volatility, plus
CPI (to report inflation-adjusted "real" value) and FX drift (currency move).

Returns nominal and real terminal-value bands (p5/p50/p95) and the probability
of a real loss (ending below today's purchasing power).
"""
from __future__ import annotations

import numpy as np

from app.core.config import Settings, get_settings
from app.schemas.simulation import Band, SimulationResult

HORIZONS = {"month": 1.0 / 12.0, "quarter": 0.25, "year": 1.0}


class SimulationEngine:
    HORIZONS = HORIZONS

    def __init__(self, settings: Settings | None = None, seed: int | None = None) -> None:
        self.settings = settings or get_settings()
        self.seed = seed

    def run(
        self,
        *,
        initial_value: float,
        expected_return_pct: float,
        volatility_pct: float,
        horizon: str = "year",
        cpi_pct: float | None = None,
        fx_change_pct: float | None = None,
        runs: int | None = None,
        seed: int | None = None,
    ) -> SimulationResult:
        s = self.settings
        if horizon not in HORIZONS:
            raise ValueError(f"horizon must be one of {tuple(HORIZONS)}")
        T = HORIZONS[horizon]
        runs = runs or s.monte_carlo_runs
        cpi = (s.sim_cpi_pct if cpi_pct is None else cpi_pct) / 100.0
        fx = (s.sim_fx_change_pct if fx_change_pct is None else fx_change_pct) / 100.0
        mu = expected_return_pct / 100.0
        sigma = volatility_pct / 100.0

        rng = np.random.default_rng(self.seed if seed is None else seed)
        z = rng.standard_normal(runs)
        gbm = np.exp((mu - 0.5 * sigma**2) * T + sigma * np.sqrt(T) * z)

        fx_factor = (1.0 + fx) ** T          # deterministic FX drift over horizon
        real_factor = 1.0 / ((1.0 + cpi) ** T)  # deflate by inflation

        nominal = initial_value * gbm * fx_factor
        real = nominal * real_factor

        def band(a: np.ndarray) -> Band:
            return Band(
                mean=float(a.mean()),
                p5=float(np.percentile(a, 5)),
                p50=float(np.percentile(a, 50)),
                p95=float(np.percentile(a, 95)),
            )

        prob_loss = float(np.mean(real < initial_value))
        return SimulationResult(
            horizon=horizon, horizon_years=T, runs=runs, initial_value=initial_value,
            expected_return=mu, volatility=sigma, cpi=cpi, fx_change=fx,
            nominal=band(nominal), real=band(real),
            probability_of_loss_real=prob_loss, probability_of_gain_real=1.0 - prob_loss,
        )
