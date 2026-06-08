"""4.4 RISK ENGINE - Monte Carlo stress, ruin probability, caps, veto.

Risk OVERRIDES return: when a position breaches the volatility cap or the
ruin-probability cap, `veto_flag` is set and the item can never be displayed,
regardless of its ROI (the orchestrator routes it to a terminal VetoedSignal).

Model: Geometric Brownian Motion price paths over `risk_horizon_years`,
`monte_carlo_runs` paths x `risk_mc_steps` steps. Probability of Ruin is the
share of paths whose peak-to-trough max drawdown breaches `max_drawdown_cap`.

Inputs (expected_return_pct, volatility_pct) come from the DetectedSignal; if
volatility is missing the engine returns 'Awaiting Data' without vetoing.
"""
from __future__ import annotations

import numpy as np

from app.core.config import Settings, get_settings
from app.schemas.risk import RiskAssessment
from app.schemas.state_machine import DetectedSignal, VettedSignal


class RiskEngine:
    def __init__(self, settings: Settings | None = None, seed: int | None = None) -> None:
        self.settings = settings or get_settings()
        self.seed = seed  # set for reproducible tests; None = random in production

    def monte_carlo(
        self,
        expected_return: float,
        volatility: float,
        *,
        horizon_years: float | None = None,
        runs: int | None = None,
        steps: int | None = None,
        seed: int | None = None,
        distribution: str | None = None,
        dof: int | None = None,
    ) -> RiskAssessment:
        """Run a GBM Monte Carlo. Rates are decimals (0.18 == 18%)."""
        s = self.settings
        runs = runs or s.monte_carlo_runs
        steps = steps or s.risk_mc_steps
        horizon = s.risk_horizon_years if horizon_years is None else horizon_years
        rng = np.random.default_rng(self.seed if seed is None else seed)
        dist = (distribution or s.risk_distribution).lower()
        df = dof or s.risk_t_dof

        dt = horizon / steps
        if dist == "t" and df > 2:
            z = rng.standard_t(df, (runs, steps)) * np.sqrt((df - 2) / df)  # unit-variance fat tails
        else:
            dist = "normal"
            z = rng.standard_normal((runs, steps))
        log_incr = (expected_return - 0.5 * volatility**2) * dt + volatility * np.sqrt(dt) * z
        rel = np.exp(np.cumsum(log_incr, axis=1))
        rel = np.concatenate([np.ones((runs, 1)), rel], axis=1)  # start at 1.0

        running_max = np.maximum.accumulate(rel, axis=1)
        drawdowns = 1.0 - rel / running_max
        path_max_dd = drawdowns.max(axis=1)
        terminal_return = rel[:, -1] - 1.0

        return RiskAssessment(
            runs=runs,
            horizon_years=horizon,
            expected_return=expected_return,
            volatility=volatility,
            probability_of_ruin=float(np.mean(path_max_dd >= s.max_drawdown_cap)),
            median_max_drawdown=float(np.median(path_max_dd)),
            worst_case_drawdown_p95=float(np.percentile(path_max_dd, 95)),
            expected_terminal_return=float(np.mean(terminal_return)),
            terminal_return_volatility=float(np.std(terminal_return)),
            distribution=dist,
            assumptions=[
                "Geometric Brownian Motion price paths",
                f"{dist} shocks" + (f" (Student-t, dof={df})" if dist == "t" else ""),
                "returns i.i.d. - no autocorrelation or volatility clustering",
                "single asset - no cross-position covariance",
                f"Probability of Ruin = P(max drawdown >= {s.max_drawdown_cap:.0%})",
            ],
        )

    def assess(self, expected_return_pct: float, volatility_pct: float) -> tuple[RiskAssessment, bool, str]:
        """Return (assessment, veto_flag, critique) for given annual percentages."""
        s = self.settings
        mu = expected_return_pct / 100.0
        sigma = volatility_pct / 100.0
        a = self.monte_carlo(mu, sigma)

        reasons: list[str] = []
        if sigma > s.volatility_cap:
            reasons.append(
                f"volatility {sigma:.0%} exceeds the {s.volatility_cap:.0%} cap"
            )
        if a.probability_of_ruin > s.ruin_probability_cap:
            reasons.append(
                f"probability of ruin {a.probability_of_ruin:.0%} exceeds the "
                f"{s.ruin_probability_cap:.0%} cap (paths breaching a "
                f"{s.max_drawdown_cap:.0%} drawdown)"
            )

        if reasons:
            return a, True, "VETO: " + "; ".join(reasons) + "."
        critique = (
            f"Within risk limits - vol {sigma:.0%}, P(ruin) "
            f"{a.probability_of_ruin:.0%}, median max drawdown "
            f"{a.median_max_drawdown:.0%}."
        )
        return a, False, critique

    def vet(self, signal: DetectedSignal) -> VettedSignal:
        """State-machine transition: attach stress metrics + veto."""
        if signal.volatility_pct is None:
            return VettedSignal(
                source=signal,
                veto_flag=False,
                risk_critique="Awaiting Data - no volatility input for Monte Carlo.",
            )
        mu_pct = signal.expected_return_pct if signal.expected_return_pct is not None else 0.0
        assessment, veto, critique = self.assess(mu_pct, signal.volatility_pct)
        return VettedSignal(
            source=signal,
            probability_of_ruin=assessment.probability_of_ruin,
            max_drawdown=assessment.median_max_drawdown,
            volatility=assessment.volatility,
            veto_flag=veto,
            risk_critique=critique,
        )
