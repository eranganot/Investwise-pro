"""4.6 SIMULATION ENGINE - forward projections (Phase 5).

Monte Carlo lognormal projection of portfolio value over a user-selected
horizon (month / quarter / year). Inputs: expected return, volatility, plus
CPI (to report inflation-adjusted "real" value) and FX drift (currency move).

Returns nominal and real terminal-value bands (p5/p50/p95) and the probability
of a real loss (ending below today's purchasing power).

W3 -- THE PROJECTION WAS BLIND TO DEPOSITS
==========================================

Until W3 this engine drew ONE multiplicative shock on the starting NAV::

    gbm = np.exp((mu - 0.5 * sigma**2) * T + sigma * np.sqrt(T) * z)
    nominal = initial_value * gbm * fx_factor

There is no time axis in that expression, so there was nowhere to put a
deposit. The assumptions list said so -- *"no rebalancing or cash flows during
the horizon"* -- and `plan.py` built its own user-facing assumptions string that
dropped the line, so the card showed a no-contribution projection without ever
saying it was one.

The asymmetry that made this worth fixing: `recommendations.py` already solves
the annuity BACKWARDS ("what monthly deposit closes the gap"), taking this
projection's shortfall as its input. So the app could tell you what deposit
closes a gap but could not show you the future in which you make it.

**THE ZERO-CONTRIBUTION PATH IS THE OLD CODE, UNCHANGED.** Not "equivalent in
distribution" -- the same expression, in a branch, producing bit-identical
numbers for the same seed. `test_the_projection_sees_your_deposits.py` pins the
old figures as literals and asserts them. That matters more than it looks:

  * Stepping and then multiplying is only distributionally equal to the single
    shot for NORMAL shocks. Under `risk_distribution = "t"` it is not -- a sum
    of Student-t draws is not Student-t -- so a "harmless" restructure would
    have quietly moved every projection of every user on the t setting.
  * Even under normal shocks the same seed gives different SAMPLES, so every
    existing projection would have moved by a little for no stated reason.

A restructure that changes numbers it did not intend to change is the failure
mode here, and a branch is a cheap price for not having it.
"""
from __future__ import annotations

import numpy as np

from app.core.config import Settings, get_settings
from app.schemas.simulation import Band, SimulationResult

HORIZONS = {"month": 1.0 / 12.0, "quarter": 0.25, "year": 1.0}

# Deposits land at the END of each step -- an ordinary annuity. Not a coin
# flip: `recommendations.py:1976-1993` ("Lever A - add money") solves
# `pmt = gap * rm / (((1 + rm) ** n) - 1)`, which is the future value of an
# ORDINARY annuity. If this engine deposited at the start of each period the two
# would disagree about the same deposit -- the card would tell you to add X and
# the projection would show a future built on more than X. Tested.
CONTRIBUTION_TIMING = "end"
CONTRIBUTION_STEPS_PER_YEAR = 12

# runs x steps floats get allocated at once. At the default 10,000 runs a
# 50-year monthly horizon is 6M doubles (~48MB), which is survivable but not
# something to discover in production. Past this the step count is reduced and
# `steps_capped` says so in the payload rather than the engine silently
# projecting something coarser than it was asked for.
MAX_STEPS = 600


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
        horizon_years: float | None = None,
        cpi_pct: float | None = None,
        fx_change_pct: float | None = None,
        runs: int | None = None,
        seed: int | None = None,
        distribution: str | None = None,
        target_value: float | None = None,
        contribution_per_year: float | None = None,
        contribution_steps_per_year: int = CONTRIBUTION_STEPS_PER_YEAR,
    ) -> SimulationResult:
        """Project terminal value, optionally with a periodic deposit.

        `contribution_per_year` is an amount in the SAME currency as
        `initial_value`, added at the end of each step at a rate of
        `contribution_per_year * dt` per step -- so the total deposited is
        exactly `contribution_per_year * T` no matter how the step count rounds.
        A step count that changed how much money you put in would be a bug the
        reader could not see.

        `None` and `0.0` both mean "no deposits" HERE, because an engine has
        nothing to say about the difference. The distinction between "no
        contributions" and "no ledger to measure one from" lives in
        `intake_service.contribution_rate`, which is the only thing that knows
        it, and is preserved there.
        """
        s = self.settings
        if horizon_years is not None:
            T = float(horizon_years)
        elif horizon in HORIZONS:
            T = HORIZONS[horizon]
        else:
            raise ValueError(f"horizon must be one of {tuple(HORIZONS)}")
        runs = runs or s.monte_carlo_runs
        cpi = (s.sim_cpi_pct if cpi_pct is None else cpi_pct) / 100.0
        fx = (s.sim_fx_change_pct if fx_change_pct is None else fx_change_pct) / 100.0
        mu = expected_return_pct / 100.0
        sigma = volatility_pct / 100.0
        contrib = 0.0 if contribution_per_year is None else float(contribution_per_year)

        rng = np.random.default_rng(self.seed if seed is None else seed)
        dist = (distribution or s.risk_distribution).lower()
        df = s.risk_t_dof
        if dist != "t" or df <= 2:
            dist = "normal"

        def shocks(shape) -> np.ndarray:
            if dist == "t":
                return rng.standard_t(df, shape) * np.sqrt((df - 2) / df)
            return rng.standard_normal(shape)

        fx_factor = (1.0 + fx) ** T          # deterministic FX drift over horizon
        real_factor = 1.0 / ((1.0 + cpi) ** T)  # deflate by inflation

        steps = 1
        steps_capped = False
        if contrib == 0.0:
            # THE ORIGINAL PATH, untouched. See the module docstring: this is a
            # branch rather than a special case of the stepped code precisely so
            # that adding contributions to the engine cannot move a projection
            # that has none.
            z = shocks(runs)
            gbm = np.exp((mu - 0.5 * sigma**2) * T + sigma * np.sqrt(T) * z)
            nominal = initial_value * gbm * fx_factor
            deposited = 0.0
        else:
            steps = max(1, int(round(T * max(1, int(contribution_steps_per_year)))))
            if steps > MAX_STEPS:
                steps, steps_capped = MAX_STEPS, True
            dt = T / steps
            per_step = contrib * dt          # so the total is contrib * T, exactly
            # FX drift is folded into the per-step growth rather than applied to
            # the terminal value. A deposit made in year 9 of a 10-year horizon
            # has been exposed to one year of currency drift, not ten; applying
            # `fx_factor` to the whole terminal amount would credit every deposit
            # with the full horizon's drift. With no deposits the two are the
            # same number, which is why the branch above can keep using it.
            step_fx = (1.0 + fx) ** dt
            drift = np.exp((mu - 0.5 * sigma**2) * dt) * step_fx
            vol_dt = sigma * np.sqrt(dt)
            z = shocks((runs, steps))
            value = np.full(runs, float(initial_value))
            for i in range(steps):
                value = value * (drift * np.exp(vol_dt * z[:, i])) + per_step
            nominal = value
            deposited = per_step * steps

        real = nominal * real_factor

        def band(a: np.ndarray) -> Band:
            return Band(
                mean=float(a.mean()),
                p5=float(np.percentile(a, 5)),
                p50=float(np.percentile(a, 50)),
                p95=float(np.percentile(a, 95)),
            )

        # KEPT AS IT WAS: "ended below the value you started with, in today's
        # money". With deposits that is no longer the same question as "did I
        # lose money" -- you put more in -- so the honest comparison gets its own
        # field below rather than quietly redefining this one. Two fields that
        # disagree is the point; one field that changed meaning is the bug.
        prob_loss = float(np.mean(real < initial_value))
        money_in = float(initial_value) + deposited
        prob_loss_money_in = float(np.mean(real < money_in))
        prob_meets = float(np.mean(nominal >= target_value)) if target_value else None

        assumptions = [
            f"lognormal terminal value under {dist} shocks",
            "deterministic CPI/FX drift over the horizon",
        ]
        if deposited:
            assumptions.append(
                f"deposits of {contrib:,.0f}/yr paid at the end of each of {steps} "
                f"steps ({deposited:,.0f} in total), no rebalancing")
        else:
            assumptions.append("no rebalancing or cash flows during the horizon")
        if steps_capped:
            assumptions.append(
                f"step count capped at {MAX_STEPS}; deposits are coarser than "
                f"{contribution_steps_per_year}/yr but total the same amount")

        return SimulationResult(
            horizon=horizon, horizon_years=T, runs=runs, initial_value=initial_value,
            expected_return=mu, volatility=sigma, cpi=cpi, fx_change=fx,
            nominal=band(nominal), real=band(real),
            probability_of_loss_real=prob_loss, probability_of_gain_real=1.0 - prob_loss,
            probability_meets_target=prob_meets,
            distribution=dist,
            contribution_per_year=contrib,
            contribution_total=round(deposited, 2),
            contribution_timing=CONTRIBUTION_TIMING,
            steps=steps,
            money_in=round(money_in, 2),
            probability_of_loss_vs_money_in=prob_loss_money_in,
            assumptions=assumptions,
        )
