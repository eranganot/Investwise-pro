"""PR 93 - does the goal survive real markets? Filtered historical paths.

PR 92 decides with a lognormal: smooth, symmetric, no crash days. The book it
decides for holds an 88%-volatility leveraged sleeve, whose real risk lives in
exactly what a lognormal leaves out -- volatility that clusters, crashes that
arrive in runs, and the compounding drag of a 3x fund through a drawdown.

So every number a goal card states is checked a second way: thousands of
paths to the user's deadline, each stitched from REAL historical days in
21-day blocks (so a crash and its aftershocks stay together), on the book's
own daily returns.

**THE DRIFT IS THE USER'S, THE SHAPE IS HISTORY'S.** Bootstrapping raw history
would silently replace the user's return confidence with the backtest's mean --
a 'shrunk' user would be shown 'measured' odds through the back door. Each
asset's daily returns are re-centred on the drift his chosen estimate assigns
it ("filtered historical simulation"): history supplies the volatility, the
tails, the correlations and the path; his estimate supplies the centre.

**DEPOSITS ARE LINEAR, SO THE LEVER IS EXACT.** Final wealth on a path is
nav * C_T + c * S, where C_T is the path's growth and S the summed growth of
a deposit made at each month-end. The deposit that gives p* of paths the goal
is therefore a percentile of (target - nav*C_T) / S -- no search.

Pure numpy, no I/O.
"""
from __future__ import annotations

import numpy as np

TRADING_DAYS = 252
MONTH_DAYS = 21
N_PATHS = 2000
BLOCK_DAYS = 21
SEED = 11


def recentre(returns: np.ndarray, drift_annual: np.ndarray, *, periods: int = TRADING_DAYS) -> np.ndarray:
    """Shift each column so its mean daily return is drift/periods; shape untouched."""
    r = np.asarray(returns, dtype=float)
    return r - r.mean(axis=0) + np.asarray(drift_annual, dtype=float) / periods


def simulate(port_daily: np.ndarray, *, years: float, n_paths: int = N_PATHS,
             block: int = BLOCK_DAYS, seed: int = SEED) -> dict:
    """Stationary-block paths of one portfolio's daily returns to `years`.

    Returns per path: `growth` (C_T), `deposit_factor` (S: the final value of 1
    deposited at every month-end) and `worst_drawdown` (the deepest fall from a
    running peak, as a positive fraction).
    """
    r = np.asarray(port_daily, dtype=float)
    t_hist = r.size
    steps = max(1, int(round(years * TRADING_DAYS)))
    if t_hist < block * 2:
        raise ValueError("not enough history to bootstrap")
    rng = np.random.default_rng(seed)
    n_blocks = -(-steps // block)
    starts = rng.integers(0, t_hist - block + 1, size=(n_paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_paths, -1)[:, :steps]
    log_c = np.cumsum(np.log1p(np.maximum(r[idx], -0.999999)), axis=1)
    growth_t = np.exp(log_c[:, -1])
    months = np.arange(MONTH_DAYS, steps + 1, MONTH_DAYS) - 1
    if months.size:
        deposit_factor = np.exp(log_c[:, -1:] - log_c[:, months]).sum(axis=1)
    else:
        deposit_factor = np.zeros(n_paths)
    peak = np.maximum.accumulate(np.concatenate([np.zeros((n_paths, 1)), log_c], axis=1), axis=1)[:, 1:]
    worst = 1.0 - np.exp((log_c - peak).min(axis=1))
    return {"growth": growth_t, "deposit_factor": deposit_factor, "worst_drawdown": worst,
            "n_paths": n_paths, "steps": steps}


def goal_stats(sim: dict, *, nav: float, target: float, monthly: float = 0.0,
               p_star: float = 0.70) -> dict:
    """What the paths say about the goal: odds, median, the bad tail, the lever."""
    g, s = sim["growth"], sim["deposit_factor"]
    final = nav * g + monthly * s
    worst5 = np.sort(final)[: max(1, int(0.05 * final.size))]
    need = (target - nav * g - monthly * s) / np.where(s > 0, s, np.inf)
    extra = float(np.percentile(need, p_star * 100))
    return {"p_goal_pct": round(float(np.mean(final >= target)) * 100, 1),
            "median": round(float(np.median(final)), 0),
            "worst5_mean": round(float(worst5.mean()), 0),
            "p_halve_pct": round(float(np.mean(sim["worst_drawdown"] >= 0.5)) * 100, 1),
            "extra_monthly_needed": max(0.0, extra) if np.isfinite(extra) else None,
            "amount_at_confidence": round(float(np.percentile(final, (1 - p_star) * 100)), 0)}
