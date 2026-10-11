"""Futures & macro-market signals (Phase: Google Finance / Gemini).

Pulls key index/commodity/rate/vol futures from Yahoo (keyless) and derives a
simple risk-on / neutral / risk-off regime that the agents factor into
recommendations. Results are cached briefly so the Today view and the
recommendation builder don't hammer the network on every request.
"""
from __future__ import annotations

import concurrent.futures
import json
import time
from datetime import datetime, timezone

from app.providers.live import _http_text

# symbol -> (label, group)
FUTURES: dict[str, tuple[str, str]] = {
    "ES=F": ("S&P 500", "equity"),
    "NQ=F": ("Nasdaq 100", "equity"),
    "YM=F": ("Dow Jones", "equity"),
    "CL=F": ("Crude Oil", "commodity"),
    "GC=F": ("Gold", "commodity"),
    "ZN=F": ("10Y T-Note", "rate"),
    "DX=F": ("US Dollar", "fx"),
    "^VIX": ("Volatility (VIX)", "vol"),
}

_CACHE: dict = {"ts": 0.0, "data": None}
_TTL_SEC = 300  # 5 min


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_one(symbol: str) -> dict | None:
    """Price + day change via Yahoo's chart API (no key)."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           "?interval=1d&range=5d")
    try:
        data = json.loads(_http_text(url))
    except Exception:  # noqa: BLE001
        return None
    result = ((data.get("chart") or {}).get("result") or [])
    if not result:
        return None
    meta = result[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    if price is None:
        return None
    # PR 108 (C11): THE PREVIOUS DAILY BAR, not `chartPreviousClose`. With
    # range=5d that field is the close BEFORE the window -- about four sessions
    # ago -- and `previousClose` is always None on this endpoint. Live on
    # 2026-10-11: ES=F showed -0.18% for a +0.56% day, NQ=F -1.19% for +0.45%.
    # The last bar is today's (or the last session's) own bar, so the one
    # before it is the previous close whether the market is open or shut.
    closes = (((result[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
    earlier = [c for c in closes[:-1] if c is not None]
    prev = earlier[-1] if earlier else None
    chg_pct = None
    if prev:
        try:
            chg_pct = round((float(price) - float(prev)) / float(prev) * 100.0, 2)
        except Exception:  # noqa: BLE001
            chg_pct = None
    label, group = FUTURES.get(symbol, (symbol, "other"))
    return {"symbol": symbol, "label": label, "group": group,
            "price": round(float(price), 2),
            "change_pct": chg_pct, "currency": str(meta.get("currency") or "USD")}


def _regime(items: list[dict]) -> dict:
    """Risk-on / neutral / risk-off from VIX level + equity-futures direction."""
    by = {i["symbol"]: i for i in items}
    vix = (by.get("^VIX") or {}).get("price")
    eq = [by[s]["change_pct"] for s in ("ES=F", "NQ=F", "YM=F")
          if by.get(s) and by[s].get("change_pct") is not None]
    eq_avg = round(sum(eq) / len(eq), 2) if eq else None

    score = 0  # >0 risk-on, <0 risk-off
    if vix is not None:
        if vix >= 22:
            score -= 2
        elif vix >= 18:
            score -= 1
        elif vix < 14:
            score += 1
    if eq_avg is not None:
        if eq_avg <= -1.0:
            score -= 2
        elif eq_avg < 0:
            score -= 1
        elif eq_avg >= 1.0:
            score += 2
        elif eq_avg > 0:
            score += 1

    if score <= -2:
        label, tone = "risk-off", "bad"
    elif score >= 2:
        label, tone = "risk-on", "ok"
    else:
        label, tone = "neutral", "warn"

    bits = []
    if vix is not None:
        bits.append(f"VIX {vix:.1f}")
    if eq_avg is not None:
        bits.append(f"equity futures {eq_avg:+.2f}%")
    rationale = ", ".join(bits) or "limited data"
    return {"regime": label, "tone": tone, "score": score,
            "vix": vix, "equity_avg_change_pct": eq_avg, "rationale": rationale}


class FuturesUnavailable(RuntimeError):
    """Every futures fetch failed. The previous snapshot was kept, marked stale."""


def futures_snapshot(force: bool = False, *, raise_on_outage: bool = False) -> dict:
    """All tracked futures + a derived market regime. Cached for _TTL_SEC.

    PR 107 (L10): when EVERY fetch fails, the previous snapshot is kept and
    marked ``stale`` instead of being overwritten by an empty one -- which read
    as a silent "neutral" regime. ``raise_on_outage`` is for the warm job, so
    the outage reaches `JOB_STATE` instead of passing as a healthy run.
    """
    now = time.time()
    if not force and _CACHE["data"] is not None and (now - _CACHE["ts"]) < _TTL_SEC:
        return _CACHE["data"]
    # fetch all symbols concurrently so one request doesn't wait on eight round-trips
    items = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(FUTURES)) as ex:
        for row in ex.map(_fetch_one, list(FUTURES.keys())):
            if row:
                items.append(row)
    order = list(FUTURES.keys())
    items.sort(key=lambda r: order.index(r["symbol"]) if r["symbol"] in order else 99)
    if not items:
        prev = _CACHE.get("data")
        if prev and prev.get("futures"):
            snap = {**prev, "stale": True, "stale_since": prev.get("stale_since") or _now()}
        else:
            snap = {"as_of": _now(), "futures": [], "market": _regime([]), "stale": True}
        _CACHE.update(ts=now, data=snap)
        if raise_on_outage:
            raise FuturesUnavailable(f"all {len(FUTURES)} futures fetches failed; "
                                     f"kept the snapshot from {snap.get('as_of')}")
        return snap
    snap = {"as_of": _now(), "futures": items, "market": _regime(items)}
    _CACHE.update(ts=now, data=snap)
    return snap


def regime() -> dict:
    """Just the regime block (fetches if the cache is cold)."""
    return futures_snapshot().get("market", {"regime": "neutral", "tone": "warn", "rationale": "no data"})


CACHED_REGIME_MAX_AGE_SEC = 1800


def cached_regime() -> dict:
    """The FUTURES regime from the cache only -- never hits the network.

    Display / cross-check only (PR 108, D-a): nothing that acts reads it; the
    regime that acts is `regime_service.cached_regime_proxy()`. Returns {} when
    cold, stale, or older than CACHED_REGIME_MAX_AGE_SEC (it had no TTL, so a
    days-old futures reading could be quoted as today's).
    """
    data = _CACHE.get("data")
    if not data or data.get("stale") or (time.time() - float(_CACHE.get("ts") or 0)) > CACHED_REGIME_MAX_AGE_SEC:
        return {}
    return data.get("market", {}) or {}
