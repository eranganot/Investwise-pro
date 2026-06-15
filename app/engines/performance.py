"""Phase E - performance metrics (pure math).

Given a portfolio value series (and a benchmark close series) over aligned dates,
compute normalized index curves, total/annualized return, max drawdown and the
excess vs the benchmark. No I/O - the service supplies the date-aligned values.
"""
from __future__ import annotations

import numpy as np


def index_series(values: list[float]) -> list[float]:
    a = np.asarray(values, dtype=float)
    base = a[0] if a.size and a[0] != 0 else 1.0
    return [round(float(x), 3) for x in (a / base * 100.0)]


def total_return(values: list[float]) -> float:
    return float(values[-1] / values[0] - 1.0) if len(values) > 1 and values[0] else 0.0


def cagr(values: list[float], periods_per_year: int = 252) -> float:
    t = len(values) - 1
    if t <= 0 or values[0] <= 0:
        return 0.0
    return float((values[-1] / values[0]) ** (periods_per_year / t) - 1.0)


def max_drawdown(values: list[float]) -> float:
    a = np.asarray(values, dtype=float)
    if a.size == 0:
        return 0.0
    peak = np.maximum.accumulate(a)
    return float((1.0 - a / peak).max())


def summarize(portfolio_values: list[float], benchmark_values: list[float] | None,
              *, periods_per_year: int = 252) -> dict:
    pr = total_return(portfolio_values)
    out = {
        "total_return_pct": round(pr * 100, 2),
        "cagr_pct": round(cagr(portfolio_values, periods_per_year) * 100, 2),
        "max_drawdown_pct": round(max_drawdown(portfolio_values) * 100, 2),
        "portfolio_index": index_series(portfolio_values),
        "benchmark_index": None,
        "benchmark_return_pct": None,
        "excess_return_pct": None,
    }
    if benchmark_values and len(benchmark_values) == len(portfolio_values):
        br = total_return(benchmark_values)
        out["benchmark_index"] = index_series(benchmark_values)
        out["benchmark_return_pct"] = round(br * 100, 2)
        out["excess_return_pct"] = round((pr - br) * 100, 2)
    return out
