"""Phase D - PORTFOLIO-LEVEL RISK (returns-based, real correlations).

Unlike the per-position Monte Carlo in risk_engine, this looks at the book as a
whole using historical daily returns:

  * covariance / correlation across holdings -> portfolio volatility (annualized)
  * historical Value-at-Risk and Conditional VaR (expected shortfall)
  * real beta vs a benchmark (regression slope), not a hand-set number
  * goal-based Monte Carlo: probability of reaching a target wealth by a date,
    using the portfolio's own mu/sigma.

Pure math, no I/O - the service layer supplies the price history.
"""
from __future__ import annotations

import numpy as np


def pct_returns(closes: list[float]) -> np.ndarray:
    a = np.asarray(closes, dtype=float)
    if a.size < 2:
        return np.array([])
    return a[1:] / a[:-1] - 1.0


def _align(series: list[np.ndarray]) -> np.ndarray:
    """Stack return series into a T x N matrix, aligned on the most recent L rows."""
    series = [s for s in series if s.size > 0]
    if not series:
        return np.empty((0, 0))
    L = min(s.size for s in series)
    return np.column_stack([s[-L:] for s in series])


def analyze(*, tickers: list[str], weights: list[float],
            history_by_ticker: dict[str, list[float]],
            benchmark_returns: np.ndarray | None = None,
            nav: float = 0.0, target: float | None = None, years: float | None = None,
            runs: int = 10000, seed: int = 7, trading_days: int = 252) -> dict:
    w = np.asarray(weights, dtype=float)
    if w.sum() <= 0:
        w = np.ones(len(tickers))
    w = w / w.sum()

    rmat = _align([pct_returns(history_by_ticker.get(t, [])) for t in tickers])
    if rmat.size == 0 or rmat.shape[0] < 2:
        return {"ok": False, "reason": "insufficient price history"}

    n = rmat.shape[1]
    w = w[:n] if w.size >= n else np.pad(w, (0, n - w.size), constant_values=0)
    w = w / w.sum() if w.sum() > 0 else np.ones(n) / n

    cov_d = np.cov(rmat, rowvar=False)
    cov_d = np.atleast_2d(cov_d)
    cov_a = cov_d * trading_days
    port_vol = float(np.sqrt(max(0.0, w @ cov_a @ w)))

    corr = np.corrcoef(rmat, rowvar=False) if n > 1 else np.array([[1.0]])
    corr = np.atleast_2d(corr)
    off = corr[~np.eye(n, dtype=bool)]
    avg_corr = float(np.mean(off)) if off.size else 0.0

    port_rets = rmat @ w
    mu_a = float(port_rets.mean() * trading_days)
    p5 = np.percentile(port_rets, 5)
    var95 = float(-p5)
    tail = port_rets[port_rets <= p5]
    cvar95 = float(-tail.mean()) if tail.size else var95

    beta = None
    if benchmark_returns is not None and benchmark_returns.size > 1:
        L = min(port_rets.size, benchmark_returns.size)
        pr, br = port_rets[-L:], np.asarray(benchmark_returns[-L:], dtype=float)
        vb = br.var()
        if vb > 0 and L > 2:
            beta = float(np.cov(pr, br)[0, 1] / vb)

    goal = None
    if target and years and nav > 0:
        rng = np.random.default_rng(seed)
        z = rng.standard_normal(runs)
        terminal = nav * np.exp((mu_a - 0.5 * port_vol ** 2) * years + port_vol * np.sqrt(years) * z)
        goal = {
            "target": round(float(target), 2), "years": round(float(years), 2),
            "prob_reach": round(float(np.mean(terminal >= target)), 4),
            "median_terminal": round(float(np.median(terminal)), 2),
            "p10_terminal": round(float(np.percentile(terminal, 10)), 2),
            "p90_terminal": round(float(np.percentile(terminal, 90)), 2),
        }

    return {
        "ok": True,
        "observations": int(rmat.shape[0]),
        "annualized_volatility_pct": round(port_vol * 100, 2),
        "annualized_return_pct": round(mu_a * 100, 2),
        "var_95_1d_pct": round(var95 * 100, 2),
        "cvar_95_1d_pct": round(cvar95 * 100, 2),
        "var_95_1d_ils": round(var95 * nav, 2) if nav else None,
        "avg_correlation": round(avg_corr, 3),
        "beta": round(beta, 3) if beta is not None else None,
        "goal": goal,
    }
