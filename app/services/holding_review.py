"""PR 77 (plan item 6, "PR E") -- a per-holding reviewer that may propose a SELL,
but only when selling on its signal has beaten simply holding.

Eran's item 6: "an agent that analyses short- and long-term trends per holding
and produces sell / partial-sell action cards". His answer to Q5: an actionable
sell only with a MEASURED edge; trend-alone sells are a backlog option.

Why the bar (STATUS B2): on TQQQ a timing overlay turned a 40.25%/yr instrument
into 17.19%, below plain QQQ, with a deeper drawdown. A trend "sell" is exactly
such an overlay, so each signal is backtested on the holding's OWN ~10 years,
net of costs and the 25% CGT (`strategy_backtest`, the engine every strategy is
measured with), against buying and holding the same ticker on the same dates.

Two signals, two measurements, run nightly (`measure_all`) and stored per
ticker; the Today build only reads them:

  * LONG  -- 50-day average below the 200-day  -> proposes EXIT (sell all);
  * SHORT -- 20-day average below the 50-day   -> proposes TRIM (`TRIM_FRACTION`).

A signal's card is ACTIONABLE only if, on the same window, the rule's CAGR beat
holding's AND its worst drawdown was no deeper. Otherwise the card is guidance
and says, with the numbers, that selling on it did worse. Unmeasured, stale or
engine-version-mismatched measurements never make a card actionable.

Known bias, stated on the card: the averages need 200 sessions to exist, and
those first sessions count as CASH for the rule while holding is invested --
it tilts every comparison toward holding, i.e. toward NOT selling.

Sleeve holdings are not reviewed for selling: the sleeve's own strategy rule
decides, and the review row reports that rule's stored signal.

Not financial advice: the app updates its tracked book; the order is Eran's.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Versioned assumptions (investing-discipline §4), owner_reviewed=False until
# Eran signs them off. The backtest measures a FULL exit on the short signal; a
# partial trim is a sizing choice, and this is it.
TRIM_FRACTION = {"id": "review.trim_fraction", "version": 1, "value": 0.5,
                 "owner_reviewed": False,
                 "why": "the short signal trims half; the long signal exits"}
SIGNALS = {
    "long": {"id": "review.signal.long", "version": 1, "fast": 50, "slow": 200,
             "action": "exit", "words": "the 50-day average fell below the 200-day"},
    "short": {"id": "review.signal.short", "version": 1, "fast": 20, "slow": 50,
              "action": "trim", "words": "the 20-day average fell below the 50-day"},
}
MAX_MEASUREMENT_AGE_DAYS = 3        # nightly job; a weekend plus a missed night
_KV_PREFIX = "holding_review:"


# --------------------------------------------------------------------------- #
# The pure half: what the prices say today
# --------------------------------------------------------------------------- #
def _sma(xs: list[float], n: int) -> float | None:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def _ret(xs: list[float], n: int) -> float | None:
    return round((xs[-1] / xs[-1 - n] - 1.0) * 100.0, 2) if len(xs) > n and xs[-1 - n] > 0 else None


def metrics(closes: list[float]) -> dict:
    """Every number the review row and the card show, from daily closes."""
    xs = [float(c) for c in (closes or []) if c is not None and float(c) > 0]
    if len(xs) < 60:
        return {"ok": False, "why": f"{len(xs)} daily closes, 60 needed"}
    px = xs[-1]
    s20, s50, s200 = _sma(xs, 20), _sma(xs, 50), _sma(xs, 200)
    hi20 = max(xs[-20:])
    hi252 = max(xs[-252:])
    rets = [xs[i] / xs[i - 1] - 1.0 for i in range(max(1, len(xs) - 20), len(xs))]
    mu = sum(rets) / len(rets) if rets else 0.0
    vol20 = (sum((r - mu) ** 2 for r in rets) / max(1, len(rets) - 1)) ** 0.5 * (252 ** 0.5) * 100
    out = {"ok": True, "price": round(px, 4),
           "sma20": round(s20, 4), "sma50": round(s50, 4),
           "sma200": round(s200, 4) if s200 is not None else None,
           "ret_1m_pct": _ret(xs, 21), "ret_6m_pct": _ret(xs, 126), "ret_12m_pct": _ret(xs, 252),
           "from_20d_high_pct": round((px / hi20 - 1.0) * 100.0, 2),
           "vol_20d_pct": round(vol20, 1),
           "drawdown_52w_pct": round((px / hi252 - 1.0) * 100.0, 2)}
    out["short"] = ("down" if s20 < s50 else "up")
    out["long"] = (None if s200 is None else "down" if s50 < s200 else "up")
    return out


def state_of(m: dict) -> str:
    """Hold | Protect | Trim | Exit, from the two crosses the signals measure."""
    if not m.get("ok"):
        return "Unmeasured"
    if m.get("long") == "down" and m.get("short") == "down":
        return "Exit"
    if m.get("short") == "down":
        return "Trim"
    if m.get("long") == "down":
        return "Protect"          # long trend broken, short recovering: a stop, not a sale
    return "Hold"


# --------------------------------------------------------------------------- #
# The measured half: did selling on the signal beat holding?
# --------------------------------------------------------------------------- #
def spec_for(ticker: str, signal: str) -> dict:
    s = SIGNALS[signal]
    return {"id": f"review_{signal}_{ticker}", "weights": {ticker: 1.0},
            "overlay": {"kind": "ma_cross", "fast": s["fast"], "slow": s["slow"]}}


def judge(rule: dict, hold: dict) -> dict:
    """The evidence bar, from two `strategy_backtest.run` results."""
    if not (rule.get("ok", True) and hold.get("ok", True)) or rule.get("cagr_pct") is None \
            or hold.get("cagr_pct") is None:
        return {"measured": False, "edge": False,
                "why": rule.get("detail") or hold.get("detail") or "backtest abstained"}
    r_c, h_c = float(rule["cagr_pct"]), float(hold["cagr_pct"])
    r_dd, h_dd = abs(float(rule.get("max_drawdown_pct") or 0)), abs(float(hold.get("max_drawdown_pct") or 0))
    edge = r_c > h_c and r_dd <= h_dd
    return {"measured": True, "edge": edge,
            "rule_cagr_pct": round(r_c, 2), "hold_cagr_pct": round(h_c, 2),
            "rule_max_drawdown_pct": round(r_dd, 2), "hold_max_drawdown_pct": round(h_dd, 2),
            "start": rule.get("start"), "end": rule.get("end"), "years": rule.get("years"),
            "round_trips": rule.get("round_trips"), "tax_drag_pct": rule.get("tax_drag_pct"),
            "why": ("beat holding with no deeper drawdown" if edge else
                    "did worse than holding" if r_c <= h_c else
                    "earned more than holding but with a deeper drawdown")}


def measure_ticker(ticker: str, series: dict, basis: dict | None = None) -> dict:
    """Both signals for one ticker, from already-fetched history (pure).

    `basis` is what `_fetch` reported per ticker; the measurement carries it so
    a price-only figure (no dividend-adjusted series) is labelled as one."""
    from app.engines import strategy_backtest as bt
    from app.services.backtest_service import ENGINE_VERSION, return_basis_of
    hold = bt.run(series, {"id": f"review_hold_{ticker}", "weights": {ticker: 1.0}})
    out = {"ticker": ticker, "engine_version": ENGINE_VERSION,
           **return_basis_of(basis if basis is not None else {ticker: "price_only"}),
           "measured_at": datetime.now(timezone.utc).isoformat(),
           "assumptions": [f"{s['id']}@{s['version']}" for s in SIGNALS.values()]}
    for name in SIGNALS:
        out[name] = judge(bt.run(series, spec_for(ticker, name)), hold)
    # PR 81: today's trend state and volatility, stored with the measurement so
    # the 30-minute rule evaluator (trend_break) and the tuning cards read it
    # without fetching history.
    try:
        closes = [float(r[1]) for r in (series.get(ticker) or []) if r and r[1] is not None]
        out["metrics"] = metrics(closes[-260:])
    except Exception:  # noqa: BLE001 - the measurement stands without it
        out["metrics"] = {"ok": False, "why": "could not read closes"}
    return out


def _key(ticker: str) -> str:
    return f"{_KV_PREFIX}{ticker.upper()}"[:80]


async def stored(session, tickers) -> dict[str, dict]:
    from app.models.tables import KVSetting
    out = {}
    for tk in tickers:
        row = await session.get(KVSetting, _key(tk))
        if row:
            try:
                out[tk.upper()] = json.loads(row.value)
            except Exception:  # noqa: BLE001
                pass
    return out


async def save(session, result: dict) -> None:
    from app.models.tables import KVSetting
    k = _key(result["ticker"])
    row = await session.get(KVSetting, k)
    if row is None:
        session.add(KVSetting(key=k, value=json.dumps(result)))
    else:
        row.value = json.dumps(result)


def fresh(m: dict | None, *, now: datetime | None = None) -> tuple[bool, str | None]:
    """Is a stored measurement fit to make a card actionable?"""
    from app.services.backtest_service import ENGINE_VERSION
    if not m:
        return False, "not measured yet (the review measures nightly)"
    if m.get("engine_version") != ENGINE_VERSION:
        return False, "measured with an older engine version"
    try:
        at = datetime.fromisoformat(m["measured_at"])
    except (KeyError, ValueError):
        return False, "no measurement date"
    age = ((now or datetime.now(timezone.utc)) - at).days
    if age > MAX_MEASUREMENT_AGE_DAYS:
        return False, f"measured {age} days ago"
    return True, None


async def measure_for(session, tickers) -> list[dict]:
    """Fetch ~10 years per ticker and store both measurements. Network-heavy:
    the nightly job and the explicit refresh call this, never the Today build."""
    from app.core.offload import offload
    from app.services.backtest_service import _fetch
    done = []
    for tk in sorted({t.upper() for t in tickers}):
        basis: dict = {}
        series, missing = await offload(_fetch, [tk], basis=basis)
        if tk not in series:
            done.append({"ticker": tk, "ok": False, "why": f"no history ({', '.join(missing) or tk})"})
            continue
        res = await offload(measure_ticker, tk, series, basis)
        await save(session, res)
        done.append({"ticker": tk, "ok": True,
                     **{n: res[n].get("edge") for n in SIGNALS}})
    await session.commit()
    return done


def _own_session():
    """A throwaway NullPool engine in the CURRENT loop. The nightly job runs from
    the scheduler's `asyncio.run` loop and the on-demand pass from its own
    thread; borrowing the app's engine there is the cross-loop failure CLAUDE.md
    names."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import get_settings
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return eng, async_sessionmaker(eng, expire_on_commit=False)


async def _measure_all_async() -> list[dict]:
    """Every held non-cash ticker in the book, measured and stored."""
    from sqlalchemy import select

    from app.models.tables import Position
    from app.services.intake_service import is_cash_position

    eng, maker = _own_session()
    try:
        async with maker() as s:
            rows = (await s.execute(select(Position.ticker, Position.meta))).all()
            tks = {t.upper() for t, m in rows
                   if t and not is_cash_position(t, m if isinstance(m, dict) else None)}
            return await measure_for(s, tks)
    finally:
        await eng.dispose()


async def _measure_these_async(tickers) -> list[dict]:
    eng, maker = _own_session()
    try:
        async with maker() as s:
            return await measure_for(s, tickers)
    finally:
        await eng.dispose()


# --------------------------------------------------------------------------- #
# On demand: a holding the nightly run has not seen (Eran, 2026-09-29, option a)
# --------------------------------------------------------------------------- #
# A holding bought today had no measurement until 04:40 tomorrow, so a broken
# trend on it could not become a sell card. The Today build now hands every
# unmeasured or stale holding to a background pass; the next Today load reads
# the result. One pass per ticker at a time, and a ticker is not retried within
# RETRY_AFTER_S -- a provider that is rate-limiting us must not be hit again on
# every page load.
RETRY_AFTER_S = 30 * 60
_inflight: set[str] = set()
_tried: dict[str, float] = {}
_LOCK = threading.Lock()


def kick_background(tickers, *, force: bool = False) -> list[str]:
    """Start measuring `tickers` in a background thread; returns the ones started.

    Off where the scheduler is off (`enable_scheduler`, false under pytest): a
    background thread that writes to the database is a second writer, and a test
    that wants one passes `force=True` and waits for it."""
    import time

    from app.core.config import get_settings
    if not (force or get_settings().enable_scheduler):
        return []
    now = time.monotonic()
    with _LOCK:
        todo = sorted({t.upper() for t in tickers if t} - _inflight)
        todo = [t for t in todo if now - _tried.get(t, -1e18) >= RETRY_AFTER_S]
        _inflight.update(todo)
        for t in todo:
            _tried[t] = now
    if not todo:
        return []

    def _work():
        import asyncio
        try:
            asyncio.run(_measure_these_async(todo))
        except Exception:  # noqa: BLE001 - the nightly run is the fallback
            logger.warning("on-demand holding review failed for %s", todo, exc_info=True)
        finally:
            with _LOCK:
                _inflight.difference_update(todo)

    th = threading.Thread(target=_work, name="holding-review-on-demand", daemon=True)
    th.start()
    kick_background.last_thread = th
    return todo


def run_holding_review_blocking() -> list[dict]:
    """The nightly job (scheduler `holding_review`, 04:40)."""
    import asyncio
    return asyncio.run(_measure_all_async())
