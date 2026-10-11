"""The ONE market regime the app acts on: the strategies' price-derived regime.

PR 108 (D-a, owner 2026-10-08). Today's "Markets look risk-off" card read the
futures regime (`markets_service.cached_regime`), while the strategies gate on
`engines/regime` (SPY's 200-day trend, realised volatility, breadth). On
2026-10-07 the two disagreed -- futures risk-off (VIX 22.5), proxy risk_on 0.95
-- and Today told the owner to "raise cash 5-10%, trim volatile positions" on
the number the code itself calls display-only. Futures stay on the Markets tab
as a labelled cross-check; everything that ACTS reads this module.

Cache-only on the Today path: `cached_regime_proxy()` never touches the network.
The `futures_warm` job and the Markets route refresh it (history comes from the
cached history tier, so a refresh is cheap once the nightly backtests ran).
"""
from __future__ import annotations

import threading
import time

PROXY_TTL_SEC = 3600          # a daily-bar regime cannot change faster than this matters
PROXY_STALE_SEC = 6 * 3600    # older than this, Today treats it as unknown

_LOCK = threading.Lock()
_CACHE: dict = {"ts": 0.0, "data": None}


def regime_proxy() -> dict:
    """Today's price-derived regime -- the one the strategies actually read.

    Calls the same ``regime.latest`` the backtest gates on. Moved here from
    ``routes/gfinance.py`` so a service can read it without importing a route.
    """
    from app.engines import regime as rg
    from app.engines.strategy_backtest import align
    from app.services.backtest_service import _fetch
    series, missing = _fetch(rg.tickers_needed())
    if not series:
        return {"ok": False, "reason": "MISSING_TICKER", "detail": ", ".join(missing)}
    _dates, px = align(series)
    out = rg.latest(px)
    if missing:
        # Say which index is absent rather than quietly narrowing breadth.
        out["degraded"] = f"no history for {', '.join(missing)}"
    return out


def refresh_regime_proxy(force: bool = False) -> dict:
    """Recompute and cache the proxy when it is older than PROXY_TTL_SEC."""
    now = time.time()
    with _LOCK:
        fresh = _CACHE["data"] is not None and (now - _CACHE["ts"]) < PROXY_TTL_SEC
        if fresh and not force:
            return _CACHE["data"]
    out = regime_proxy()
    if out.get("ok"):
        with _LOCK:
            _CACHE.update(ts=time.time(), data=out)
    return out


def cached_regime_proxy() -> dict:
    """The cached proxy, or {} when cold or older than PROXY_STALE_SEC. Never fetches."""
    with _LOCK:
        data, ts = _CACHE["data"], _CACHE["ts"]
    if not data or (time.time() - ts) > PROXY_STALE_SEC:
        return {}
    return {**data, "age_sec": round(time.time() - ts)}


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def describe(proxy: dict) -> str:
    """The proxy's components in words, for a card that has to say why."""
    c = proxy.get("components") or {}
    bits = []
    if c.get("trend_up") is not None:
        bits.append("SPY above its 200-day trend" if c["trend_up"] else "SPY below its 200-day trend")
    if c.get("vol_percentile") is not None:
        bits.append(f"volatility at the {_ordinal(round(c['vol_percentile']))} percentile of its history")
    if c.get("breadth") is not None:
        bits.append(f"{c['breadth']:.0%} of SPY/QQQ/IWM in an uptrend")
    return ", ".join(bits) or "no components reported"
