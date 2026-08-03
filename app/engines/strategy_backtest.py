"""Strategy backtest engine - measure a strategy instead of assuming it (pure math, no I/O).

Why this exists
---------------
``strategy_profile`` derives a basket's risk/return from a lookup table of
instrument characters. That is honest for a static buy-and-hold basket, but it
cannot describe a *rule*: "hold TQQQ only while QQQ is above its 200-day" has no
entry in any assumption table, and inventing one would be exactly the fabricated
number CLAUDE.md forbids.

So these strategies are measured, not assumed. Feeding the engine a leveraged
fund's own price series means decay, expense ratio and financing cost are
already *in the data* - there is nothing left to estimate.

Two costs that backtests usually omit and this one does not:

* **Israeli CGT at 25%**, deducted at the moment of the realizing sale (which is
  how an Israeli broker withholds it), with losses banked to offset later gains.
  A swing system that turns over its sleeve monthly pays tax annually where
  buy-and-hold defers it indefinitely, so omitting this can overstate a
  high-turnover strategy by several points a year.
* **Spread/commission** in basis points on every traded leg.

Lookahead is avoided structurally: a signal computed from the close of day *t*
is executed at the close of day *t+1*. Every public entry point either returns a
measured result or abstains with a typed ``reason`` - never a filled-in guess.

Nothing here is a forecast. A backtest describes one path through one sample of
history; ``out_of_sample`` and ``sweep`` exist to show how fragile that is.
"""
from __future__ import annotations

import numpy as np

from app.engines.performance import cagr, max_drawdown

TRADING_DAYS = 252

# Reasons we refuse to produce a number. Callers surface these verbatim rather
# than rendering a strategy card with blanks or zeros.
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
MISSING_TICKER = "MISSING_TICKER"
NO_OVERLAP = "NO_OVERLAP"
UNKNOWN_OVERLAY = "UNKNOWN_OVERLAY"
BAD_SPEC = "BAD_SPEC"

_MIN_OBSERVATIONS = 260  # ~1 trading year; below this, CAGR is noise

CGT_RATE = 0.25          # Israel, individual, no long-term holding relief
DEFAULT_COST_BPS = 5.0   # per traded leg, round-trip spread + commission


def _abstain(reason: str, detail: str = "") -> dict:
    return {"ok": False, "reason": reason, "detail": detail}


# --------------------------------------------------------------------------
# indicators (all return arrays aligned to the input, NaN where undefined)
# --------------------------------------------------------------------------

def sma(a: np.ndarray, n: int) -> np.ndarray:
    out = np.full(a.shape, np.nan)
    if n <= 0 or a.size < n:
        return out
    c = np.cumsum(np.insert(a, 0, 0.0))
    out[n - 1:] = (c[n:] - c[:-n]) / float(n)
    return out


def rsi(a: np.ndarray, n: int) -> np.ndarray:
    """Wilder-smoothed RSI. Short windows (n=2) are the Connors pullback variant."""
    out = np.full(a.shape, np.nan)
    if a.size <= n or n <= 0:
        return out
    d = np.diff(a)
    gain, loss = np.clip(d, 0, None), -np.clip(d, None, 0)
    ag, al = gain[:n].mean(), loss[:n].mean()
    for i in range(n, d.size + 1):
        if i > n:
            ag = (ag * (n - 1) + gain[i - 1]) / n
            al = (al * (n - 1) + loss[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


def rolling_max(a: np.ndarray, n: int) -> np.ndarray:
    out = np.full(a.shape, np.nan)
    for i in range(n - 1, a.size):
        out[i] = a[i - n + 1:i + 1].max()
    return out


def rolling_min(a: np.ndarray, n: int) -> np.ndarray:
    out = np.full(a.shape, np.nan)
    for i in range(n - 1, a.size):
        out[i] = a[i - n + 1:i + 1].min()
    return out


def _confirm(flag: np.ndarray, days: int) -> np.ndarray:
    """Require ``days`` consecutive True before flipping on.

    A raw moving-average cross whipsaws: price ticks below the line, the system
    sells, it ticks back three sessions later and rebuys - two taxable events for
    no move. Confirmation trades some lateness for far fewer round trips.
    """
    if days <= 1:
        return flag
    out = np.zeros(flag.shape, dtype=bool)
    run = 0
    for i, f in enumerate(flag):
        run = run + 1 if f else 0
        out[i] = run >= days
    return out


# --------------------------------------------------------------------------
# alignment
# --------------------------------------------------------------------------

def align(series: dict[str, list[tuple[str, float]]]) -> tuple[list[str], dict[str, np.ndarray]]:
    """Intersect dates across every ticker so all arrays share one calendar."""
    if not series:
        return [], {}
    maps = {tk: {d: float(c) for d, c in rows} for tk, rows in series.items()}
    common = set.intersection(*[set(m) for m in maps.values()])
    dates = sorted(common)
    return dates, {tk: np.array([m[d] for d in dates], dtype=float) for tk, m in maps.items()}


# --------------------------------------------------------------------------
# overlays -> a target-weight dict per day
# --------------------------------------------------------------------------

def _weights_or_off(on: np.ndarray, weights: dict[str, float], risk_off: str | None) -> list[dict]:
    off = {risk_off: 1.0} if risk_off else {}
    return [dict(weights) if bool(flag) else dict(off) for flag in on]


def _trend_filter(px: dict[str, np.ndarray], spec: dict) -> np.ndarray:
    """Hold the sleeve while the gate ticker sits above its long moving average.

    This is what makes leverage survivable: the daily-reset penalty scales with
    variance, and variance clusters below the long-term trend. Sitting out those
    stretches removes most of the decay rather than paying it.
    """
    o = spec["overlay"]
    gate = px[o["gate_ticker"]]
    line = sma(gate, int(o.get("ma_days", 200)))
    band = 1.0 + float(o.get("band_pct", 0.0)) / 100.0
    return _confirm(np.nan_to_num(gate > line * band, nan=False), int(o.get("confirm_days", 1)))


def _ma_cross(px: dict[str, np.ndarray], spec: dict) -> np.ndarray:
    o = spec["overlay"]
    base = px[o.get("gate_ticker") or next(iter(spec["weights"]))]
    fast, slow = sma(base, int(o.get("fast", 20))), sma(base, int(o.get("slow", 50)))
    on = np.nan_to_num(fast > slow, nan=False)
    if o.get("ma_days"):  # optional long-trend gate on top of the cross
        on = on & np.nan_to_num(base > sma(base, int(o["ma_days"])), nan=False)
    return _confirm(on, int(o.get("confirm_days", 1)))


def _donchian(px: dict[str, np.ndarray], spec: dict) -> np.ndarray:
    """Turtle-style channel breakout: enter on an N-day high, exit on an M-day low.

    Two parameters and a long published record, which makes it the hardest of
    these setups to curve-fit.
    """
    o = spec["overlay"]
    base = px[o.get("gate_ticker") or next(iter(spec["weights"]))]
    hi = rolling_max(base, int(o.get("entry_days", 20)))
    lo = rolling_min(base, int(o.get("exit_days", 10)))
    on = np.zeros(base.shape, dtype=bool)
    holding = False
    for i in range(base.size):
        if np.isnan(hi[i]) or np.isnan(lo[i]):
            continue
        if not holding and base[i] >= hi[i]:
            holding = True
        elif holding and base[i] <= lo[i]:
            holding = False
        on[i] = holding
    return on


def _rsi_pullback(px: dict[str, np.ndarray], spec: dict) -> np.ndarray:
    """Buy short-term oversold, but only inside a long-term uptrend.

    Mean reversion without the trend gate is how traders catch falling knives;
    the gate confines it to the regime where dips have historically recovered.
    Applied to a leveraged sleeve this is the 'gated dip-buy' variant.
    """
    o = spec["overlay"]
    gate_tk = o.get("gate_ticker") or next(iter(spec["weights"]))
    entry_tk = o.get("signal_ticker") or gate_tk
    gate, sig = px[gate_tk], px[entry_tk]
    uptrend = np.nan_to_num(gate > sma(gate, int(o.get("ma_days", 200))), nan=False)
    r = rsi(sig, int(o.get("rsi_days", 2)))
    exit_line = sma(sig, int(o.get("exit_ma", 5)))
    entry_level = float(o.get("entry", 10.0))
    on = np.zeros(sig.shape, dtype=bool)
    holding = False
    for i in range(sig.size):
        if holding:
            # exit on strength, or immediately if the regime gate closes
            if (not np.isnan(exit_line[i]) and sig[i] > exit_line[i]) or not uptrend[i]:
                holding = False
        elif uptrend[i] and not np.isnan(r[i]) and r[i] < entry_level:
            holding = True
        on[i] = holding
    return on


def _dual_momentum(px: dict[str, np.ndarray], spec: dict) -> list[dict]:
    """Relative momentum picks the leader; absolute momentum vetoes a falling one."""
    o = spec["overlay"]
    look = int(o.get("lookback_days", 252))
    universe = list(o.get("universe") or spec["weights"].keys())
    safe = o.get("risk_off") or spec.get("risk_off")
    n = len(next(iter(px.values())))
    out: list[dict] = []
    for i in range(n):
        if i < look:
            out.append({safe: 1.0} if safe else {})
            continue
        rets = {tk: px[tk][i] / px[tk][i - look] - 1.0 for tk in universe if tk in px}
        best = max(rets, key=rets.get) if rets else None
        if best is None or rets[best] <= 0:      # absolute momentum veto
            out.append({safe: 1.0} if safe else {})
        else:
            out.append({best: 1.0})
    return out


def targets_for(px: dict[str, np.ndarray], spec: dict) -> list[dict] | dict:
    """Target weights per day, or an abstention dict when the spec is unusable."""
    kind = (spec.get("overlay") or {}).get("kind", "buy_hold")
    weights, risk_off = spec.get("weights") or {}, spec.get("risk_off")
    if not weights and kind != "dual_momentum":
        return _abstain(BAD_SPEC, "strategy has no basket weights")
    if kind == "buy_hold":
        return [dict(weights)] * len(next(iter(px.values())))
    if kind == "dual_momentum":
        return _dual_momentum(px, spec)
    fn = {"trend_filter": _trend_filter, "ma_cross": _ma_cross,
          "donchian": _donchian, "rsi_pullback": _rsi_pullback}.get(kind)
    if fn is None:
        return _abstain(UNKNOWN_OVERLAY, f"no overlay named '{kind}'")
    return _weights_or_off(fn(px, spec), weights, risk_off)


# --------------------------------------------------------------------------
# simulation
# --------------------------------------------------------------------------

def _simulate(px: dict[str, np.ndarray], targets: list[dict], *,
              cost_bps: float, cgt_rate: float) -> dict:
    """Walk the calendar, trading at the close one day after each signal.

    Positions are share counts; ``basis`` is the average cost per share. A sale
    realizes gain against that basis, offsets it against banked losses, and pays
    the remainder at ``cgt_rate`` out of the proceeds - so tax reduces the money
    that goes on compounding, which is the whole point.
    """
    n = len(targets)
    shares: dict[str, float] = {}
    basis: dict[str, float] = {}
    entry_px: dict[str, float] = {}
    cash = 100.0
    values, tax_paid, traded_notional = [], 0.0, 0.0
    loss_bank, trades, wins, closed = 0.0, 0, 0, 0

    for i in range(n):
        held = sum(shares.get(tk, 0.0) * px[tk][i] for tk in shares)
        value = cash + held
        values.append(value)
        # signal from close i-1 executes at close i: no lookahead
        want = targets[i - 1] if i > 0 else {}
        cur = {tk: (shares.get(tk, 0.0) * px[tk][i]) / value for tk in shares} if value > 0 else {}
        drift = sum(abs(want.get(tk, 0.0) - cur.get(tk, 0.0))
                    for tk in set(want) | set(cur))
        if drift < 0.01 or value <= 0:
            continue

        for tk in list(shares):                                   # sells first
            tgt_val = want.get(tk, 0.0) * value
            cur_val = shares[tk] * px[tk][i]
            if cur_val - tgt_val <= value * 0.005:
                continue
            sell_sh = (cur_val - tgt_val) / px[tk][i]
            proceeds = sell_sh * px[tk][i]
            gain = (px[tk][i] - basis.get(tk, px[tk][i])) * sell_sh
            if gain < 0:
                loss_bank += -gain
            else:
                offset = min(loss_bank, gain)
                loss_bank -= offset
                tax = (gain - offset) * cgt_rate
                tax_paid += tax
                proceeds -= tax
            cost = abs(sell_sh * px[tk][i]) * cost_bps / 10000.0
            cash += proceeds - cost
            traded_notional += abs(sell_sh * px[tk][i])
            shares[tk] -= sell_sh
            trades += 1
            if shares[tk] * px[tk][i] < value * 0.005:             # round trip closed
                closed += 1
                if px[tk][i] > entry_px.get(tk, px[tk][i]):
                    wins += 1
                shares.pop(tk, None)
                basis.pop(tk, None)
                entry_px.pop(tk, None)

        for tk, w in want.items():                                 # then buys
            tgt_val = w * value
            cur_val = shares.get(tk, 0.0) * px[tk][i]
            if tgt_val - cur_val <= value * 0.005:
                continue
            spend = min(tgt_val - cur_val, cash)
            if spend <= 0:
                continue
            cost = spend * cost_bps / 10000.0
            buy_sh = (spend - cost) / px[tk][i]
            prev_sh = shares.get(tk, 0.0)
            basis[tk] = ((basis.get(tk, 0.0) * prev_sh + buy_sh * px[tk][i])
                         / (prev_sh + buy_sh)) if prev_sh + buy_sh else px[tk][i]
            entry_px.setdefault(tk, px[tk][i])
            shares[tk] = prev_sh + buy_sh
            cash -= spend
            traded_notional += spend
            trades += 1

    return {"values": values, "tax_paid": tax_paid, "trades": trades,
            "traded_notional": traded_notional, "round_trips": closed, "wins": wins}


def _metrics(sim: dict, dates: list[str], bench: np.ndarray | None) -> dict:
    values = sim["values"]
    v = np.asarray(values, dtype=float)
    rets = np.diff(v) / np.where(v[:-1] == 0, 1.0, v[:-1])
    years = max(len(values) / TRADING_DAYS, 1e-9)
    out = {
        "cagr_pct": round(cagr(values) * 100, 2),
        "volatility_pct": round(float(rets.std(ddof=1)) * (TRADING_DAYS ** 0.5) * 100, 2)
        if rets.size > 1 else 0.0,
        "max_drawdown_pct": round(max_drawdown(values) * 100, 2),
        "total_return_pct": round((values[-1] / values[0] - 1.0) * 100, 2),
        "trades_per_year": round(sim["trades"] / years, 1),
        "turnover_per_year": round(sim["traded_notional"] / 100.0 / years, 2),
        "round_trips": sim["round_trips"],
        "win_rate_pct": round(100.0 * sim["wins"] / sim["round_trips"], 1)
        if sim["round_trips"] else None,
        "start": dates[0], "end": dates[-1], "observations": len(dates),
        "years": round(years, 2),
    }
    if bench is not None and bench.size:
        b = cagr(list(bench)) * 100
        out["benchmark_cagr_pct"] = round(b, 2)
        out["excess_cagr_pct"] = round(out["cagr_pct"] - b, 2)
        out["benchmark_max_drawdown_pct"] = round(max_drawdown(list(bench)) * 100, 2)
    return out


# --------------------------------------------------------------------------
# public entry points
# --------------------------------------------------------------------------

def tickers_needed(spec: dict) -> list[str]:
    """Every symbol the spec references, so the caller knows what to fetch."""
    o = spec.get("overlay") or {}
    names = set(spec.get("weights") or {})
    for key in ("gate_ticker", "signal_ticker", "risk_off"):
        if o.get(key):
            names.add(o[key])
    names.update(o.get("universe") or [])
    if spec.get("risk_off"):
        names.add(spec["risk_off"])
    return sorted(names)


def run(series: dict[str, list[tuple[str, float]]], spec: dict, *,
        benchmark: list[tuple[str, float]] | None = None,
        cgt_rate: float = CGT_RATE, cost_bps: float = DEFAULT_COST_BPS) -> dict:
    """Backtest one spec. Returns measured metrics, or abstains with a reason."""
    missing = [tk for tk in tickers_needed(spec) if tk not in series]
    if missing:
        return _abstain(MISSING_TICKER, f"no price history for {', '.join(missing)}")

    feed = dict(series)
    if benchmark:
        feed["__bench__"] = benchmark
    dates, px = align(feed)
    if not dates:
        return _abstain(NO_OVERLAP, "the tickers share no common trading dates")
    if len(dates) < _MIN_OBSERVATIONS:
        return _abstain(INSUFFICIENT_HISTORY,
                        f"{len(dates)} overlapping sessions; need {_MIN_OBSERVATIONS}")
    bench = px.pop("__bench__", None)

    targets = targets_for(px, spec)
    if isinstance(targets, dict):        # an abstention leaked through
        return targets

    sim = _simulate(px, targets, cost_bps=cost_bps, cgt_rate=cgt_rate)
    out = _metrics(sim, dates, bench)

    # Tax drag is the honest headline for a high-turnover strategy: the same
    # path re-run tax-free, differenced. Buy-and-hold defers CGT indefinitely;
    # a swing system pays it as it goes, and that gap compounds.
    gross = _simulate(px, targets, cost_bps=cost_bps, cgt_rate=0.0)
    gross_cagr = cagr(gross["values"]) * 100
    out.update({
        "ok": True,
        "gross_cagr_pct": round(gross_cagr, 2),
        "tax_drag_pct": round(gross_cagr - out["cagr_pct"], 2),
        "tax_paid_pct_of_start": round(sim["tax_paid"], 2),
        "cgt_rate_pct": round(cgt_rate * 100, 1),
        "cost_bps": cost_bps,
        "overlay": (spec.get("overlay") or {}).get("kind", "buy_hold"),
        "disclaimer": ("Backtested over the stated period on real closing prices, "
                       "net of 25% CGT and dealing costs. A backtest is one path "
                       "through one sample of history, not a forecast."),
    })
    return out


def out_of_sample(series: dict[str, list[tuple[str, float]]], spec: dict, *,
                  split_date: str, **kw) -> dict:
    """Fit-period vs test-period metrics.

    A strategy tuned until it looked good on the whole sample will show a
    healthy in-sample CAGR and a limp out-of-sample one. Splitting before the
    2022 bear market is the useful cut: it is the only real drawdown most of
    these instruments have lived through.
    """
    def _slice(rows, lo=None, hi=None):
        return [(d, c) for d, c in rows if (lo is None or d >= lo) and (hi is None or d < hi)]

    ins = run({tk: _slice(r, hi=split_date) for tk, r in series.items()}, spec, **kw)
    oos = run({tk: _slice(r, lo=split_date) for tk, r in series.items()}, spec, **kw)
    verdict = None
    if ins.get("ok") and oos.get("ok"):
        decay = ins["cagr_pct"] - oos["cagr_pct"]
        verdict = ("holds up" if decay <= 3 else
                   "weaker out of sample" if decay <= 10 else "likely overfitted")
    return {"split_date": split_date, "in_sample": ins, "out_of_sample": oos,
            "verdict": verdict}


def sweep(series: dict[str, list[tuple[str, float]]], spec: dict,
          param: str, values: list, **kw) -> dict:
    """Vary one overlay parameter and report the spread of outcomes.

    A setup whose CAGR collapses when the moving average moves from 200 to 190
    days is fitted to noise, and the card should say so rather than print the
    single flattering number.
    """
    rows = []
    for v in values:
        trial = {**spec, "overlay": {**(spec.get("overlay") or {}), param: v}}
        r = run(series, trial, **kw)
        rows.append({"value": v, "cagr_pct": r.get("cagr_pct") if r.get("ok") else None,
                     "max_drawdown_pct": r.get("max_drawdown_pct") if r.get("ok") else None,
                     "reason": None if r.get("ok") else r.get("reason")})
    got = [r["cagr_pct"] for r in rows if r["cagr_pct"] is not None]
    return {"param": param, "results": rows,
            "cagr_spread_pct": round(max(got) - min(got), 2) if len(got) > 1 else None,
            "robust": (max(got) - min(got) <= 5.0) if len(got) > 1 else None}
