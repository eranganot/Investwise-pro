"""Build LagObservations from real price history instead of hardcoded samples.

The war room used to run on `demo_data.DEFAULT_OBSERVATIONS` — TEVA at a made-up
spot of 100 against a made-up listing of 108.2 — and then presented the result as
a real "Approved: Buy TEVA" decision. Nothing downstream could tell the numbers
were invented, which is exactly what `CLAUDE.md` forbids.

Everything here is derived from observed closes:

* ``spot_price``     — the latest close.
* ``listing_price``  — the trailing simple moving average (the price's own
  trend). Divergence is therefore "how far has this moved from its own trend",
  a computed quantity rather than an assumed fair value.
* ``volatility_pct`` — realized annualized volatility from daily returns.
* ``depth``          — how *persistent* the divergence is: a gap that has held
  for weeks is structural (3); a one-week spike is surface hype (1).
* ``expected_return_pct`` — the divergence itself, i.e. the return implied *if*
  the price reverts to trend. A modeling assumption, not a forecast, and
  labelled as such wherever it surfaces.

No fabricated prices, no invented targets. When history is unavailable for a
ticker it is skipped rather than filled in with a placeholder.
"""
from __future__ import annotations

import logging
import math

from app.schemas.lag import LagObservation
from app.schemas.state_machine import ActionType, Market

logger = logging.getLogger(__name__)

# Trend reference window, in trading days.
_MA_WINDOW = 50
# Minimum closes needed before a signal is trustworthy at all.
_MIN_HISTORY = 30
# Persistence thresholds (fraction of the recent window on one side of trend).
_BACKBONE_PERSISTENCE = 0.80
_MID_PERSISTENCE = 0.60


def _closes(raw) -> list[float]:
    """Normalize the two provider shapes: [float] (Yahoo) and [(date, float)] (FMP)."""
    out: list[float] = []
    for item in raw or []:
        try:
            v = float(item[1]) if isinstance(item, (tuple, list)) else float(item)
        except (TypeError, ValueError, IndexError):
            continue
        if v > 0:
            out.append(v)
    return out


def realized_vol_pct(closes: list[float], window: int = 60) -> float | None:
    """Annualized volatility from daily log returns (None when too little data)."""
    tail = closes[-(window + 1):]
    if len(tail) < 10:
        return None
    rets = [math.log(tail[i] / tail[i - 1]) for i in range(1, len(tail)) if tail[i - 1] > 0]
    if len(rets) < 5:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100.0


def _depth_from_persistence(closes: list[float], ma: float) -> int:
    """A divergence that has held for weeks is structural; a blip is hype."""
    recent = closes[-20:]
    if not recent:
        return 1
    above = sum(1 for c in recent if c > ma)
    share = max(above, len(recent) - above) / len(recent)
    if share >= _BACKBONE_PERSISTENCE:
        return 3
    if share >= _MID_PERSISTENCE:
        return 2
    return 1


def observation_for(ticker: str, market: str, closes: list[float]) -> LagObservation | None:
    """Turn one price series into a grounded observation (None if unusable)."""
    closes = _closes(closes)
    if len(closes) < _MIN_HISTORY:
        return None
    spot = closes[-1]
    window = closes[-_MA_WINDOW:]
    ma = sum(window) / len(window)
    if spot <= 0 or ma <= 0:
        return None
    try:
        mk = Market(market) if market in {m.value for m in Market} else Market.NYSE
    except ValueError:
        mk = Market.NYSE
    divergence_pct = (ma - spot) / spot * 100.0
    return LagObservation(
        ticker=ticker.upper(),
        market=mk,
        depth=_depth_from_persistence(closes, ma),
        spot_price=round(spot, 4),
        listing_price=round(ma, 4),
        # Below trend -> a buy candidate; above trend -> a trim/rebalance candidate.
        action_type=ActionType.BUY if divergence_pct > 0 else ActionType.REBALANCE,
        expected_return_pct=round(divergence_pct, 2),
        volatility_pct=(round(v, 2) if (v := realized_vol_pct(closes)) is not None else None),
    )


def build_observations(candidates: list[dict], *, limit: int = 25) -> list[LagObservation]:
    """Fetch history for each candidate and build grounded observations.

    candidates: [{"ticker": str, "market": str}, ...]. Failures are skipped and
    logged — a provider hiccup must never resurrect fabricated sample data.
    """
    from app.providers.registry import guarded_history

    out: list[LagObservation] = []
    for c in candidates[:limit]:
        ticker = (c.get("ticker") or "").upper()
        if not ticker or ticker == "CASH":
            continue
        try:
            obs = observation_for(ticker, (c.get("market") or "NYSE").upper(),
                                  guarded_history(ticker, 252))
        except Exception:  # noqa: BLE001 — one bad ticker must not sink the scan
            logger.warning("signal history failed for %s", ticker, exc_info=True)
            continue
        if obs is not None:
            out.append(obs)
    return out


def candidate_set(positions, *, extra: int = 12) -> list[dict]:
    """Holdings first, then a slice of the screener universe as new ideas.

    Every name in the universe is eligible \u2014 the agents rank the whole field
    and recommend which to buy, rather than the user pre-filtering it.
    """
    seen: set[str] = set()
    held: list[dict] = []
    for p in positions or []:
        tk = (getattr(p, "ticker", "") or "").upper()
        if tk and tk != "CASH" and tk not in seen:
            seen.add(tk)
            held.append({"ticker": tk, "market": (getattr(p, "market", "") or "NYSE").upper()})

    ideas: list[dict] = []
    try:
        from app.services import universe as _u
        for row in _u.equity_universe():
            if len(ideas) >= extra:
                break
            tk = (row.get("ticker") or "").upper()
            if tk and tk not in seen:
                seen.add(tk)
                ideas.append({"ticker": tk, "market": (row.get("market") or "NYSE").upper()})
    except Exception:  # noqa: BLE001
        logger.warning("universe unavailable for signal candidates", exc_info=True)

    return held + ideas
