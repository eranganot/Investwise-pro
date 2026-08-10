"""A market regime that can be backtested, because the backtest and the live
read call this same function.

THE CONSTRAINT THAT DECIDES THE DESIGN
--------------------------------------
The live regime shown on the Markets page comes from the Yahoo **futures** feed,
which has no usable history. If the live signal read futures and the backtest
read something else, they would be different rules -- and the measured numbers on
a strategy card would stop describing what actually runs. That is the same
dishonesty this whole build exists to remove, just better hidden.

So the regime is **price-derived**, computed from index series we already hold a
decade of, and this module is pure: arrays in, arrays out, no I/O, no clock, no
provider. ``strategy_backtest`` gates on ``series()`` over historical closes;
``strategy_signal_service`` calls the identical function on recent closes and
reads the last element. There is one rule, and it is the one that was measured.

Futures stay on the Markets page as a labelled cross-check, never an input.

WHY REALIZED VOLATILITY AND NOT VIX
-----------------------------------
VIX is a better forward-looking signal and the wrong tool here: it is not a
tradeable price series, it is another provider dependency that can fail or go
stale, and using it would reintroduce exactly the live/backtest divergence above.
Realized volatility from closes we already fetch is computable identically in
both paths, which is the property that matters.

WHAT IT MEASURES
----------------
Three components, each a plain, checkable fact about prices:

* **trend** -- is the index above its 200-day average?
* **volatility state** -- is realized volatility high against *its own* trailing
  history? Judged as a percentile of itself rather than against a fixed number,
  because "20% vol" means something different for the Nasdaq than for the S&P,
  and something different in 2017 than in 2022.
* **breadth** -- what share of a small index set is above its own 200-day? One
  index rising alone is a narrower, more fragile advance than all of them rising.

Scored, not ANDed. Requiring all three would make the gate fire on any single
component wobbling; a score lets one weak leg sit inside ``neutral`` instead of
slamming to ``risk_off``.
"""
from __future__ import annotations

import numpy as np

RISK_ON = "risk_on"
NEUTRAL = "neutral"
RISK_OFF = "risk_off"

# The index set. SPY carries the trend and the volatility state; the breadth
# proxy asks how many of these are above their own 200-day. Deliberately small
# and liquid -- a breadth measure nobody can reproduce is not evidence.
PRIMARY = "SPY"
BREADTH_SET = ("SPY", "QQQ", "IWM")

MA_DAYS = 200
VOL_DAYS = 20              # realized vol window, ~1 trading month
VOL_LOOKBACK = 504         # ~2y of its own history to take the percentile against
VOL_HIGH_PCTL = 80.0       # above this percentile of its own history = stressed
VOL_CALM_PCTL = 50.0


def tickers_needed() -> list[str]:
    """Everything ``series()`` requires. The caller fetches; this module never does."""
    return sorted({PRIMARY, *BREADTH_SET})


def _sma(a: np.ndarray, n: int) -> np.ndarray:
    out = np.full(a.shape, np.nan)
    if n <= 0 or a.size < n:
        return out
    c = np.cumsum(np.insert(a, 0, 0.0))
    out[n - 1:] = (c[n:] - c[:-n]) / float(n)
    return out


def realized_vol(closes: np.ndarray, days: int = VOL_DAYS) -> np.ndarray:
    """Annualised realized volatility, in percent, aligned to ``closes``."""
    out = np.full(closes.shape, np.nan)
    if closes.size <= days or days <= 1:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        rets = np.diff(np.log(closes))
    for i in range(days, rets.size + 1):
        w = rets[i - days:i]
        if np.isfinite(w).all():
            out[i] = float(np.std(w, ddof=1) * np.sqrt(252.0) * 100.0)
    return out


def _trailing_percentile(a: np.ndarray, lookback: int) -> np.ndarray:
    """Where each value sits within its OWN trailing window, 0-100.

    Expanding until ``lookback`` is available, so the early part of a series is
    scored against what history exists rather than abstaining outright.
    """
    out = np.full(a.shape, np.nan)
    for i in range(a.size):
        lo = max(0, i - lookback + 1)
        w = a[lo:i + 1]
        w = w[np.isfinite(w)]
        if w.size < 20 or not np.isfinite(a[i]):
            continue
        out[i] = float((w <= a[i]).sum()) / float(w.size) * 100.0
    return out


def series(px: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Per-day regime over aligned closes.

    ``px`` maps ticker -> closes, already aligned to a shared date axis (use
    ``strategy_backtest.align``). Returns arrays of the same length:

        state          object array of risk_on / neutral / risk_off
        score          -1.0 .. +1.0, the raw blend behind the state
        trend_up       bool, primary index above its 200-day
        vol_pct        realized volatility, percent annualised
        vol_percentile where that vol sits in its own trailing history
        breadth        share of the index set above its own 200-day, 0..1

    Warm-up days, where the 200-day does not exist yet, are ``neutral`` -- not
    risk_on. An unknown regime must never read as a green light.
    """
    primary = px.get(PRIMARY)
    if primary is None or primary.size == 0:
        return {}
    n = primary.size

    trend_up = np.zeros(n, dtype=bool)
    ma = _sma(primary, MA_DAYS)
    valid_trend = np.isfinite(ma)
    trend_up[valid_trend] = primary[valid_trend] > ma[valid_trend]

    vol = realized_vol(primary)
    vol_pctl = _trailing_percentile(vol, VOL_LOOKBACK)

    # Breadth over whichever of the set the caller actually supplied. Missing
    # members shrink the denominator rather than counting as "not above".
    above = np.zeros(n, dtype=float)
    counted = np.zeros(n, dtype=float)
    for tk in BREADTH_SET:
        a = px.get(tk)
        if a is None or a.size != n:
            continue
        m = _sma(a, MA_DAYS)
        ok = np.isfinite(m)
        counted[ok] += 1.0
        above[ok] += (a[ok] > m[ok]).astype(float)
    breadth = np.divide(above, counted, out=np.full(n, np.nan), where=counted > 0)

    # Score: trend is the backbone, breadth confirms it, volatility can veto.
    score = np.zeros(n, dtype=float)
    score += np.where(trend_up, 0.5, -0.5)
    score += np.where(np.isfinite(breadth), (np.nan_to_num(breadth) - 0.5) * 0.6, 0.0)
    stressed = np.isfinite(vol_pctl) & (vol_pctl >= VOL_HIGH_PCTL)
    calm = np.isfinite(vol_pctl) & (vol_pctl <= VOL_CALM_PCTL)
    score += np.where(stressed, -0.4, 0.0)
    score += np.where(calm, 0.15, 0.0)

    state = np.full(n, NEUTRAL, dtype=object)
    state[score >= 0.35] = RISK_ON
    state[score <= -0.35] = RISK_OFF
    # Before the 200-day exists there is no trend to speak of. Unknown is
    # neutral, never risk_on: a warm-up window must not read as permission.
    state[~valid_trend] = NEUTRAL

    return {"state": state, "score": score, "trend_up": trend_up,
            "vol_pct": vol, "vol_percentile": vol_pctl, "breadth": breadth}


def latest(px: dict[str, np.ndarray]) -> dict:
    """Today's regime, as a plain dict. The live path's entry point.

    Deliberately the last element of exactly the same computation the backtest
    gates on -- not a parallel implementation that could drift from it.
    """
    s = series(px)
    if not s:
        return {"ok": False, "reason": "NO_SERIES",
                "detail": f"no closes supplied for {PRIMARY}"}
    i = -1

    def _f(key):
        v = s[key][i]
        return None if v is None or (isinstance(v, float) and not np.isfinite(v)) else float(v)

    return {
        "ok": True,
        "state": str(s["state"][i]),
        "score": round(float(s["score"][i]), 3),
        "components": {
            "trend_up": bool(s["trend_up"][i]),
            "vol_pct": None if _f("vol_pct") is None else round(_f("vol_pct"), 1),
            "vol_percentile": None if _f("vol_percentile") is None else round(_f("vol_percentile"), 1),
            "breadth": None if _f("breadth") is None else round(_f("breadth"), 2),
        },
        "inputs": {"primary": PRIMARY, "breadth_set": list(BREADTH_SET),
                   "ma_days": MA_DAYS, "vol_days": VOL_DAYS},
    }


def gate(px: dict[str, np.ndarray], *, block: tuple[str, ...] = (RISK_OFF,)) -> np.ndarray:
    """Boolean per-day mask: True where the regime permits holding the sleeve.

    This is what ``strategy_backtest`` multiplies its target weights by, and it
    is derived from the same ``series()`` the live read uses.
    """
    s = series(px)
    if not s:
        return np.zeros(0, dtype=bool)
    st = s["state"]
    return ~np.isin(st.astype(str), np.array(block, dtype=str))
