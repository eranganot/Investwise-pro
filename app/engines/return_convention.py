"""PR 76 -- ONE return convention for every projection.

Every forward return this app feeds a projection -- a strategy's measured
out-of-sample CAGR, the instrument table's rough annual rate, the objective
table's 8.5% -- is a COMPOUND (geometric) rate: what the money grows at, year
over year, with the ups and downs already inside it. The table's rows were
confirmed with Eran on 2026-09-28 to be read the same way ("treat them as
compound rates too").

`SimulationEngine` draws ``exp((mu - sigma^2/2) T + sigma sqrt(T) z)``: its
``mu`` is the arithmetic drift, and the MEDIAN lands at ``exp((mu - sigma^2/2) T)``.
Feeding it a compound rate as ``mu`` subtracts the volatility drag a SECOND
time. Measured on Eran's book (₪25,148, 24.98%/yr, 44.62% vol, 6 years): median
₪61,049 where compounding at 24.98% gives ₪95,840. The risk report
(`engines/portfolio_risk.py`, "drift_convention": "cagr") and the target solver
already did this right; /plan/projection and the behind-goal card did not.

`drift_pct` converts once, here, so the median compounds at the stated rate and
the mean sits above it by the drag.
"""
from __future__ import annotations

import math

CONVENTION = "compound"      # every stated return is a CAGR; the engine gets drift_pct(...)
CONVENTION_NOTE = ("Returns are compound annual rates, so the median outcome grows at the "
                   "stated rate. Until 2026-09-28 the projection subtracted volatility a "
                   "second time and showed a much lower median.")


def drift_pct(compound_pct: float, volatility_pct: float) -> float:
    """The arithmetic drift (%) whose engine median compounds at `compound_pct`."""
    sigma = max(0.0, float(volatility_pct)) / 100.0
    g = float(compound_pct) / 100.0
    if g <= -1.0:
        return float(compound_pct)
    # Not rounded: this feeds the simulation and is never displayed.
    return (math.log1p(g) + 0.5 * sigma ** 2) * 100.0
