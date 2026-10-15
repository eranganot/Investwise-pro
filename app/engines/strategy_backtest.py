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

from app.engines import regime as rg

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

# Overlays that were measured and did not hold up. Any consumer showing a
# strategy built on one of these must show the caveat with it -- an engine that
# quietly serves a known-broken rule is worse than one that has no rule at all.
MEASURED_FAILURES = {
    "drawdown_brake": ("Did not reduce drawdown when measured (TQQQ 2016-2026: "
                       "83.7% vs 81.8% simply held) and failed out of sample "
                       "(58.8% in-sample vs 3.2% out). Experimental only."),
}

# How much annual return the regime gate may give up before it stops being
# worth having. Decided: the gate's job is sitting out the bad stretches, so it
# is judged on drawdown -- but a filter that costs more than a point a year is
# buying that shelter too dearly. Judging on CAGR alone would reject a gate doing
# exactly what it was built to do.
MAX_CAGR_GIVEUP_PCT = 1.0

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

def align(series: dict[str, list[tuple[str, float]]], *,
          late: set[str] | frozenset = frozenset()) -> tuple[list[str], dict[str, np.ndarray]]:
    """Intersect dates across every ticker so all arrays share one calendar.

    ``late`` (PR 126) names tickers that JOIN the calendar rather than define
    it: a stock-selection universe whose members listed at different times.
    Intersecting them would cut a 44-stock backtest to MNDY's 2021 listing. A
    late ticker is NaN before its first price and carried forward over a gap
    after it, and a selection rule treats NaN as "not eligible yet".
    """
    if not series:
        return [], {}
    maps = {tk: {d: float(c) for d, c in rows} for tk, rows in series.items()}
    anchor = [m for tk, m in maps.items() if tk not in late]
    if anchor:
        dates = sorted(set.intersection(*[set(m) for m in anchor]))
    else:
        # Every ticker joins late and nothing anchors the calendar: use every
        # date any of them traded. Intersecting would cut the whole universe to
        # its youngest member -- the defect `late` exists to prevent.
        dates = sorted(set().union(*[set(m) for m in maps.values()]))
    out: dict[str, np.ndarray] = {}
    for tk, m in maps.items():
        if tk not in late:
            out[tk] = np.array([m[d] for d in dates], dtype=float)
            continue
        arr = np.full(len(dates), np.nan)
        last = np.nan
        for i, d in enumerate(dates):
            v = m.get(d)
            if v is not None:
                last = v
            arr[i] = last
        out[tk] = arr
    return dates, out


def clean_spikes(rows: list[tuple[str, float]], *, factor: float = 2.0,
                 tol: float = 0.10) -> tuple[list[tuple[str, float]], int]:
    """Drop one-day prints that jump by ``factor`` or more and come straight back.

    Phase 0 (2026-10-13) found TSEM.TA closing at 1,066 on 2017-05-04 between
    ~7,700 either side -- a 7x error a momentum or stop rule would act on. A
    3x fund's real crash days (TECL ~30% in March 2020) must survive, so the
    test is a 2x jump whose two neighbours agree within ``tol``: a price that
    left and came back, not a market that moved.
    """
    if len(rows) < 3:
        return list(rows), 0
    drop: set[int] = set()
    for i in range(1, len(rows) - 1):
        a, b, c = rows[i - 1][1], rows[i][1], rows[i + 1][1]
        if a <= 0 or b <= 0 or c <= 0:
            continue
        jump = max(b / a, a / b)
        if jump >= factor and abs(c / a - 1.0) <= tol:
            drop.add(i)
    return [r for i, r in enumerate(rows) if i not in drop], len(drop)


# --------------------------------------------------------------------------
# overlays -> a target-weight dict per day
# --------------------------------------------------------------------------

def _weights_or_off(on: np.ndarray, weights: dict[str, float], risk_off: str | None,
                    base: dict[str, float] | None = None) -> list[dict]:
    """What the sleeve holds when the setup is not live.

    Originally this fell back to cash, which quietly decided the answer: a swing
    setup is only live perhaps 10-15% of the time, so parking the other 85% in
    T-bills and then judging the result as a ten-year CAGR measures a savings
    account with a strategy attached. A swing rule is an *overlay* -- it decides
    between the aggressive instrument and the core holding, not between the
    aggressive instrument and being out of the market. ``base`` is that core;
    ``risk_off`` remains for strategies that genuinely should sit in cash when
    their regime gate closes.
    """
    off = dict(base) if base else ({risk_off: 1.0} if risk_off else {})
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


def _vol_target(px: dict[str, np.ndarray], spec: dict) -> list[dict]:
    """Scale exposure so realized risk stays roughly constant.

    Leverage decay scales with variance, and variance clusters. Holding a fixed
    3x through a volatility spike pays the largest possible penalty at the worst
    possible moment. Targeting a constant risk level shrinks exposure exactly
    when the penalty is biggest -- it attacks the decay term itself rather than
    trying to time direction.
    """
    o = spec["overlay"]
    tk = next(iter(spec["weights"]))
    gate = px[o.get("gate_ticker") or tk]
    look, target = int(o.get("vol_days", 20)), float(o.get("target_vol_pct", 20.0))
    cap, floor = float(o.get("max_weight", 1.0)), float(o.get("min_weight", 0.0))
    base = spec.get("base") or ({spec["risk_off"]: 1.0} if spec.get("risk_off") else {})
    # Hysteresis. Recomputing the target weight daily and acting on every wobble
    # produced 342 trades a year in testing -- at 25% CGT and a dealing spread
    # that is a strategy whose costs eat its own edge. Only move when the target
    # has drifted past a band.
    band = float(o.get("rebalance_band", 0.15))
    r = np.diff(gate) / gate[:-1]
    out: list[dict] = []
    held = None
    for i in range(gate.size):
        if i < look:
            out.append(dict(base))
            continue
        rv = float(r[i - look:i].std(ddof=1)) * (TRADING_DAYS ** 0.5) * 100
        w = cap if rv <= 0 else min(cap, max(floor, target / rv))
        w = round(w, 2)
        if held is not None and abs(w - held) < band:
            w = held
        held = w
        tgt = {tk: w}
        for bk, bw in base.items():          # remainder rides in the core, not cash
            tgt[bk] = tgt.get(bk, 0.0) + bw * (1.0 - w)
        out.append({k: v for k, v in tgt.items() if v > 0.005})
    return out


def _drawdown_brake(px: dict[str, np.ndarray], spec: dict) -> np.ndarray:
    """Cut exposure once the drawdown from peak passes a limit; re-enter on recovery.

    KEPT, BUT IT DOES NOT WORK -- see ``MEASURED_FAILURES``. The idea is sound
    (a 3x fund's 80% drawdown is the point where people sell and never return,
    so the backtested CAGR is unreachable in practice) but the implementation
    cannot deliver it: by the time a 25% fall has registered you have already
    taken the 25%, and re-entry on a bounce walks straight into the next leg
    down. On TQQQ 2016-2026 it capped nothing (83.7% drawdown vs 81.8% simply
    held) and split 58.8% in-sample against 3.2% out-of-sample.

    Left in place so the failure stays visible and reproducible rather than
    being rediscovered, and because a stop measured on entry price rather than
    on peak may behave differently. Do not build a shipped strategy on it.
    """
    o = spec["overlay"]
    base = px[o.get("gate_ticker") or next(iter(spec["weights"]))]
    limit = float(o.get("max_drawdown_pct", 25.0)) / 100.0
    recover = float(o.get("reenter_pct", 10.0)) / 100.0
    on = np.zeros(base.shape, dtype=bool)
    peak, trough, holding = base[0], base[0], True
    for i, pxi in enumerate(base):
        peak = max(peak, pxi)
        if holding:
            if pxi <= peak * (1.0 - limit):
                holding, trough = False, pxi
        else:
            trough = min(trough, pxi)
            if pxi >= trough * (1.0 + recover):
                holding, peak = True, pxi
        on[i] = holding
    return on


def _sector_momentum(px: dict[str, np.ndarray], spec: dict) -> list[dict]:
    """Hold the top-N of a universe on trailing return, equally weighted."""
    o = spec["overlay"]
    look, top_n = int(o.get("lookback_days", 126)), int(o.get("top_n", 3))
    universe = [t for t in (o.get("universe") or []) if t in px]
    safe = o.get("risk_off") or spec.get("risk_off")
    base = spec.get("base") or ({safe: 1.0} if safe else {})
    hold = int(o.get("hold_days", 21))         # rebalance cadence, not daily churn
    n = len(next(iter(px.values())))
    out, current = [], dict(base)
    for i in range(n):
        if i >= look and i % hold == 0:
            rets = {t: px[t][i] / px[t][i - look] - 1.0 for t in universe}
            winners = [t for t, v in sorted(rets.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
                       if v > 0]                # absolute-momentum veto
            current = ({t: 1.0 / len(winners) for t in winners} if winners else dict(base))
        out.append(dict(current))
    return out


# --------------------------------------------------------------------------
# PR 126 -- selection: rank a universe, hold the winners
# --------------------------------------------------------------------------
#
# Every earlier overlay decides WHEN to hold a fixed basket. These decide WHAT
# to hold (docs/plans/BEAT_MARKET_EXPANSION_2026-10-13.md). One function covers
# the plan's ranking strategies, so their differences are settings in the
# catalogue -- universe, score, filters, weights, cadence -- and not code paths
# that can drift apart.
#
# A name is eligible on a day only when it has a finite price on that day AND
# on every day its score reads. Late listings arrive as NaN (`align(late=...)`),
# so a 2021 IPO simply cannot be picked before it has the history to be ranked.

def _ok(a: np.ndarray, *idx: int) -> bool:
    return all(0 <= j < a.size and np.isfinite(a[j]) and a[j] > 0 for j in idx)


def _vol(a: np.ndarray, i: int, n: int) -> float | None:
    if not _ok(a, i - n, i):
        return None
    w = a[i - n:i + 1]
    if not np.all(np.isfinite(w)):
        return None
    r = np.diff(w) / w[:-1]
    v = float(r.std(ddof=1)) * (TRADING_DAYS ** 0.5)
    return v if v > 0 else None


def score_at(a: np.ndarray, i: int, kind: str, o: dict) -> float | None:
    """One name's score on day ``i``. Higher is better. None = not eligible."""
    if kind == "mom_12_1":                # 12-month return skipping the latest month
        return a[i - 21] / a[i - 252] - 1.0 if _ok(a, i - 252, i - 21, i) else None
    if kind == "risk_adj_mom":            # 12-1 return per unit of 1-year volatility
        m = score_at(a, i, "mom_12_1", o)
        v = _vol(a, i, 252)
        return m / v if m is not None and v else None
    if kind == "blend":                   # mean of the 1-, 3- and 6-month returns
        looks = o.get("blend_days") or (21, 63, 126)
        if not _ok(a, i, *[i - n for n in looks]):
            return None
        return float(np.mean([a[i] / a[i - n] - 1.0 for n in looks]))
    if kind == "ret":
        n = int(o.get("lookback_days", 126))
        return a[i] / a[i - n] - 1.0 if _ok(a, i - n, i) else None
    if kind == "low_vol":                 # calmest first
        v = _vol(a, i, int(o.get("vol_days", 252)))
        return -v if v else None
    if kind == "high_52w":                # nearness to the 52-week high
        if not _ok(a, i - 251, i):
            return None
        w = a[i - 251:i + 1]
        return float(a[i] / np.nanmax(w)) if np.all(np.isfinite(w)) else None
    raise ValueError(f"unknown score '{kind}'")


def _trend_ok(a: np.ndarray, i: int, n: int) -> bool:
    if not _ok(a, i - n + 1, i):
        return False
    w = a[i - n + 1:i + 1]
    return bool(np.all(np.isfinite(w)) and a[i] > w.mean())


def _pick(ranked: list[str], held: list[str], top: int, keep_rank: int) -> list[str]:
    """Hold the top ``top``; a name already held stays while it ranks within
    ``keep_rank``. The buffer is a tax decision: at 25% CGT on every sale,
    churning between the 10th and 11th name costs more than ranking sharper earns."""
    rank = {t: k + 1 for k, t in enumerate(ranked)}
    keep = [t for t in held if t in rank and rank[t] <= keep_rank][:top]
    for t in ranked:
        if len(keep) >= top:
            break
        if t not in keep:
            keep.append(t)
    return keep


def _weights_for(px: dict[str, np.ndarray], i: int, names: list[str], slots: int,
                 o: dict) -> dict[str, float]:
    if not names:
        return {}
    share = len(names) / float(slots)          # empty slots fall to the base
    if o.get("weighting") == "inverse_vol":
        inv = {t: 1.0 / v for t in names if (v := _vol(px[t], i, int(o.get("vol_days", 252))))}
        if len(inv) == len(names):
            tot = sum(inv.values())
            return {t: share * w / tot for t, w in inv.items()}
    return {t: share / len(names) for t in names}


def _with_base(picks: dict[str, float], base: dict[str, float]) -> dict:
    rest = max(0.0, 1.0 - sum(picks.values()))
    out = dict(picks)
    for bk, bw in base.items():
        out[bk] = out.get(bk, 0.0) + bw * rest
    return {k: v for k, v in out.items() if v > 0.0005}


def _select_two_stage(px, i, o, ts, held, base_kind):
    """H3: rank the sectors, keep the top N in an uptrend with enough names, then
    rank the stocks inside each."""
    sectors = ts["sectors"]
    min_names = int(ts.get("min_names", 2))
    elig = {}
    for etf, stocks in sectors.items():
        if etf not in px or not _trend_ok(px[etf], i, int(ts.get("sector_trend_days", 200))):
            continue
        scored = {t: v for t in stocks if t in px
                  and (v := score_at(px[t], i, base_kind, o)) is not None}
        sc = score_at(px[etf], i, "ret", {"lookback_days": int(ts.get("sector_lookback_days", 126))})
        if sc is not None and len(scored) >= min_names:
            elig[etf] = (sc, scored)
    ranked_sec = sorted(elig, key=lambda e: elig[e][0], reverse=True)
    held_sec = []
    for t in held:
        sec = next((e for e, ss in sectors.items() if t in ss), None)
        if sec and sec not in held_sec:
            held_sec.append(sec)
    chosen = _pick(ranked_sec, held_sec, int(ts.get("top_sectors", 2)),
                   int(ts.get("sector_exit_rank", 3)))
    names = []
    for e in chosen:
        scored = elig[e][1]
        ranked = sorted(scored, key=scored.get, reverse=True)
        mine = [t for t in held if t in scored]
        names += _pick(ranked, mine, int(ts.get("per_sector", 2)), int(ts.get("stock_exit_rank", 4)))
    return names, int(ts.get("top_sectors", 2)) * int(ts.get("per_sector", 2))


def _rank_select(px: dict[str, np.ndarray], spec: dict) -> list[dict]:
    o = spec["overlay"]
    base = dict(spec.get("base") or {})
    kind = o.get("score", "mom_12_1")
    universe = [t for t in (o.get("universe") or []) if t in px]
    top = int(o.get("top_n", 10))
    keep_rank = int(o.get("buffer", top))
    cad = int(o.get("cadence_days", 21))
    hold_map = o.get("hold_map") or {}
    margin = float(o.get("switch_margin", 0.0))
    veto = o.get("trend_veto_days")
    ts = o.get("two_stage")
    n = len(next(iter(px.values())))

    gate_on = np.ones(n, dtype=bool)
    if o.get("market_gate"):
        g = o["market_gate"]
        arr = px[g["ticker"]]
        gate_on = _confirm(np.nan_to_num(arr - sma(arr, int(g.get("ma_days", 200))),
                                         nan=-1.0) > 0, int(g.get("confirm_days", 3)))

    out: list[dict] = []
    held: list[str] = []
    current = dict(base)
    last = -10 ** 9
    was_on = True
    for i in range(n):
        if not gate_on[i]:
            held, current, was_on = [], dict(base), False
            out.append(dict(current))
            continue
        due = (i - last >= cad) or not was_on
        was_on = True
        if due:
            if ts:
                names, slots = _select_two_stage(px, i, o, ts, held, kind)
            else:
                scored = {}
                for t in universe:
                    v = score_at(px[t], i, kind, o)
                    if v is None:
                        continue
                    if veto and not _trend_ok(px[t], i, int(veto)):
                        continue
                    if hold_map and not _ok(px.get(hold_map.get(t, t), np.array([])), i):
                        continue
                    scored[t] = v
                if o.get("require_positive"):
                    scored = {t: v for t, v in scored.items() if v > 0}
                ranked = sorted(scored, key=scored.get, reverse=True)
                names = _pick(ranked, held, top, keep_rank)
                if margin and held and names and names[0] not in held and held[0] in scored \
                        and scored[names[0]] - scored[held[0]] < margin:
                    names = held[:top]           # VH2: switch only on a clear lead
                if not names and o.get("fallback"):
                    fb = {t: v for t in o["fallback"] if t in px
                          and (v := score_at(px[t], i, kind, o)) is not None}
                    names = sorted(fb, key=fb.get, reverse=True)[:1]
                slots = top
            last = i
            held = names
            picks = _weights_for(px, i, names, slots, o)
            if hold_map:
                picks = {hold_map.get(t, t): w for t, w in picks.items()}
            current = _with_base(picks, base) if (names or base) else {}
        out.append(dict(current))
    return out


def equal_hold_targets(px: dict[str, np.ndarray], universe: list[str],
                       cadence_days: int = 21) -> list[dict]:
    """Every name in the universe that has a price, equally weighted, re-set on
    the same cadence -- the "did the ranking add anything?" baseline."""
    names = [t for t in universe if t in px]
    n = len(next(iter(px.values())))
    out, current, last = [], {}, -10 ** 9
    for i in range(n):
        if i - last >= cadence_days:
            live = [t for t in names if _ok(px[t], i)]
            if live:
                current, last = {t: 1.0 / len(live) for t in live}, i
        out.append(dict(current))
    return out


def own_hold_spec(spec: dict) -> dict:
    """What the strategy would be if it held its own instruments and did nothing.

    Gate test 3 (approved 2026-10-13) judges decay across the 2022 split against
    THIS, not against SPY: simply holding TQQQ fell from 68.7%/yr to 15.6%/yr
    across that split while SPY fell from 18.4% to 12.3%, so measured against
    SPY every 3x rule looked overfitted -- including one with no parameters.
    """
    o = spec.get("overlay") or {}
    uni = list(o.get("universe") or [])
    if o.get("kind") == "rank_select" and o.get("two_stage"):
        uni = sorted({t for ss in o["two_stage"]["sectors"].values() for t in ss})
    if o.get("kind") in ("rank_select", "sector_momentum", "dual_momentum") and uni:
        hm = o.get("hold_map") or {}
        return {"id": f"{spec.get('id')}__own", "weights": {},
                "overlay": {"kind": "equal_hold", "universe": [hm.get(t, t) for t in uni],
                            "cadence_days": int(o.get("cadence_days", 21))}}
    if o.get("kind") == "vol_managed":
        # Its own instruments, held at its ceiling: the exposure it reaches for.
        w = exposure_mix(float(o.get("max_exposure", 1.0)), o["legs"])
    else:
        w = dict(spec.get("weights") or {}) or dict(spec.get("base") or {})
    return {"id": f"{spec.get('id')}__own", "weights": w, "overlay": {"kind": "buy_hold"},
            "drift_band": 1.0}


def late_tickers(spec: dict) -> set[str]:
    """Tickers that join the calendar instead of defining it (see `align`)."""
    o = spec.get("overlay") or {}
    if o.get("kind") not in ("rank_select", "equal_hold"):
        return set()
    names = set(o.get("universe") or []) | set(o.get("fallback") or [])
    names |= set((o.get("hold_map") or {}).values())
    ts = o.get("two_stage") or {}
    for etf, stocks in (ts.get("sectors") or {}).items():
        names.add(etf)
        names.update(stocks)
    return names


# --------------------------------------------------------------------------
# PR 128 -- leverage over a longer history, and risk parity
# --------------------------------------------------------------------------
#
# SYNTHETIC LEVERAGED FUNDS. The 3x funds start 2008-2015, so the ten-year
# window is all a backtest has ever seen -- and every strategy here was designed
# by someone who had seen that decade. The long-history test (gate, plan
# section 3) rebuilds each fund from its index back to 2000: L x the daily index
# move, less the fund fee and the cost of borrowing the extra (L-1) at the
# 13-week T-bill plus a spread. Calibrated on the overlap 2011-2026 (measured
# 2026-10-13, synthetic minus real CAGR at a 0.75% spread): TQQQ -0.6, UPRO -0.3,
# TMF -0.9, SOXL (from SOXX) +0.1, TECL +1.4, QLD -0.4, CURE +0.3, ERX +2.1,
# FAS -5.0. Daily return correlation 0.98-0.999.
SYNTH_FEE = 0.0095
SYNTH_SPREAD = 0.0075
SYNTHETIC = {
    "TQQQ": ("QQQ", 3), "QLD": ("QQQ", 2), "UPRO": ("SPY", 3), "TMF": ("TLT", 3),
    "SOXL": ("SOXX", 3), "TECL": ("XLK", 3), "FAS": ("XLF", 3), "CURE": ("XLV", 3),
    "ERX": ("XLE", 3), "LABU": ("XBI", 3), "DRN": ("XLRE", 3),
    # PR 133: the wave-3 diversifiers' leveraged and inverse legs.
    "SSO": ("SPY", 2), "UBT": ("TLT", 2), "UGL": ("GLD", 2),
    "PSQ": ("QQQ", -1), "SH": ("SPY", -1),
}
RATE_TICKER = "^IRX"        # 13-week T-bill yield, percent
CASH_LIKE = {"BIL"}         # rebuilt from the T-bill yield before it listed


def synthetic_leveraged(under: list[tuple[str, float]], lev: float,
                        rate: list[tuple[str, float]], *, fee: float = SYNTH_FEE,
                        spread: float = SYNTH_SPREAD) -> list[tuple[str, float]]:
    r = dict(rate)
    last = next(iter(r.values()), 0.0)
    out = [(under[0][0], 100.0)]
    v = 100.0
    for (_, a), (d, b) in zip(under, under[1:]):
        last = r.get(d, last)
        if lev < 0:
            # PR 133: an INVERSE fund borrows nothing. Its NAV sits in cash at the
            # T-bill rate and its short swap receives financing less a spread, so
            # it earns about (1 - lev) x the rate less |lev| x the spread. The
            # leveraged formula below would book that as negative borrowing.
            carry = (1 - lev) * last / 100.0 - abs(lev) * spread - fee
            v *= 1 + lev * (b / a - 1) + carry / TRADING_DAYS
        else:
            v *= 1 + lev * (b / a - 1) - (fee + (lev - 1) * (last / 100.0 + spread)) / TRADING_DAYS
        out.append((d, max(v, 1e-6)))
    return out


def synthetic_cash(rate: list[tuple[str, float]]) -> list[tuple[str, float]]:
    out, v = [], 100.0
    for d, y in rate:
        v *= 1 + (y / 100.0) / TRADING_DAYS
        out.append((d, v))
    return out


def _risk_parity(px: dict[str, np.ndarray], spec: dict) -> list[dict]:
    """VH1: legs sized inverse to their own recent volatility, each held only
    while ITS market is above its long average; an off leg's share sits in cash."""
    o = spec["overlay"]
    legs: dict = o["legs"]                         # fund -> the index that gates it
    cash = o.get("cash", "BIL")
    vol_days, trend = int(o.get("vol_days", 60)), int(o.get("trend_days", 200))
    cad = int(o.get("cadence_days", 21))
    on = {f: _confirm(np.nan_to_num(px[g] - sma(px[g], trend), nan=-1.0) > 0,
                      int(o.get("confirm_days", 1))) for f, g in legs.items()}
    n = len(next(iter(px.values())))
    out, w, last = [], {}, -10 ** 9
    for i in range(n):
        if i - last >= cad:
            inv = {f: 1.0 / v for f in legs if (v := _vol(px[f], i, vol_days))}
            if len(inv) == len(legs):
                tot = sum(inv.values())
                w, last = {f: x / tot for f, x in inv.items()}, i
        if not w:
            out.append({})
            continue
        t = {}
        for f, x in w.items():
            key = f if on[f][i] else cash
            t[key] = t.get(key, 0.0) + x
        out.append(t)
    return out


# --------------------------------------------------------------------------
# PR 130 -- round 2: volatility-managed exposure (approved 2026-10-13)
# --------------------------------------------------------------------------
#
# Exposure to an index = min(max_exposure, target_vol / its recent realised
# volatility) (Moreira & Muir, 2017): risk is cut when volatility rises, which
# historically comes before the worst falls. The exposure is expressed in real
# funds: below 1x the index plus cash, between 1x and 2x the index plus its 2x
# fund, between 2x and 3x the 2x fund plus the 3x fund. Re-set only when the
# exposure moves past `rebalance_band`, as `_vol_target` does.

def exposure_mix(e: float, legs: dict[str, str]) -> dict[str, float]:
    """`legs` = {"cash": .., "x1": .., "x2": .., "x3": ..}; e in [0, 3]."""
    e = max(0.0, float(e))
    if e <= 1.0:
        out = {legs["x1"]: e, legs["cash"]: 1.0 - e}
    elif e <= 2.0:
        out = {legs["x2"]: e - 1.0, legs["x1"]: 2.0 - e}
    else:
        out = {legs["x3"]: min(1.0, e - 2.0), legs["x2"]: max(0.0, 3.0 - e)}
    return {k: v for k, v in out.items() if v > 0.0005}


def _vol_managed(px: dict[str, np.ndarray], spec: dict) -> list[dict]:
    o = spec["overlay"]
    legs = o["legs"]
    sig = px[o.get("signal_ticker") or legs["x1"]]
    look = int(o.get("vol_days", 20))
    target = float(o["target_vol_pct"])
    cap = float(o.get("max_exposure", 1.0))
    band = float(o.get("rebalance_band", 0.15))
    r = np.diff(sig) / sig[:-1]
    out: list[dict] = []
    held = None
    for i in range(sig.size):
        if i < look:
            out.append({})
            continue
        rv = float(r[i - look:i].std(ddof=1)) * (TRADING_DAYS ** 0.5) * 100
        e = cap if rv <= 0 else min(cap, target / rv)
        e = round(e, 2)
        if held is not None and abs(e - held) < band:
            e = held
        held = e
        out.append(exposure_mix(e, legs))
    return out


def _trend_gated(px: dict[str, np.ndarray], spec: dict, targets: list[dict]) -> list[dict]:
    """R7: an overlay's targets while the trend is up, `off` while it is not."""
    g = (spec.get("overlay") or {}).get("trend_gate")
    if not g:
        return targets
    arr = px[g["ticker"]]
    on = _confirm(np.nan_to_num(arr - sma(arr, int(g.get("ma_days", 200))), nan=-1.0) > 0,
                  int(g.get("confirm_days", 3)))
    off = dict(g.get("off") or {})
    return [t if on[i] else dict(off) for i, t in enumerate(targets)]


def targets_for(px: dict[str, np.ndarray], spec: dict) -> list[dict] | dict:
    """Target weights per day, or an abstention dict when the spec is unusable."""
    kind = (spec.get("overlay") or {}).get("kind", "buy_hold")
    weights, risk_off = spec.get("weights") or {}, spec.get("risk_off")
    if not weights and kind not in ("dual_momentum", "rank_select", "equal_hold", "vol_managed"):
        return _abstain(BAD_SPEC, "strategy has no basket weights")
    if kind == "buy_hold":
        return [dict(weights)] * len(next(iter(px.values())))
    if kind == "rank_select":
        return _rank_select(px, spec)
    if kind == "risk_parity":
        return _risk_parity(px, spec)
    if kind == "equal_hold":
        o = spec["overlay"]
        return equal_hold_targets(px, list(o.get("universe") or []), int(o.get("cadence_days", 21)))
    if kind == "dual_momentum":
        return _dual_momentum(px, spec)
    if kind == "sector_momentum":
        return _sector_momentum(px, spec)
    if kind == "vol_target":
        return _trend_gated(px, spec, _vol_target(px, spec))
    if kind == "vol_managed":
        return _vol_managed(px, spec)
    fn = {"trend_filter": _trend_filter, "ma_cross": _ma_cross,
          "donchian": _donchian, "rsi_pullback": _rsi_pullback,
          "drawdown_brake": _drawdown_brake}.get(kind)
    if fn is None:
        return _abstain(UNKNOWN_OVERLAY, f"no overlay named '{kind}'")
    return _weights_or_off(fn(px, spec), weights, risk_off, spec.get("base"))


# --------------------------------------------------------------------------
# simulation
# --------------------------------------------------------------------------

def _simulate(px: dict[str, np.ndarray], targets: list[dict], *,
              cost_bps: float, cgt_rate: float, drift_band: float = 0.01) -> dict:
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
    entry_i: dict[str, int] = {}
    # Per-round-trip results. A days-to-weeks strategy cannot be judged on CAGR
    # alone: one deployed 12% of the time may have an excellent per-trade edge
    # and simply be under-deployed, which is a sizing problem, not a bad rule.
    trip_pnl: list[float] = []
    trip_days: list[int] = []
    cash = 100.0
    values, tax_paid, traded_notional = [], 0.0, 0.0
    loss_bank, trades, wins, closed = 0.0, 0, 0, 0
    # The target the book was last traded to. A NEW target is acted on at the
    # 1% floor; a book merely drifting from an UNCHANGED target is re-trued only
    # past `drift_band` (PR 125). With the band at its 0.01 default the two are
    # the same test and every existing caller is unchanged.
    last_traded: dict | None = None

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
        # Measured on btm_vol_target_tqqq before this existed: its target moved
        # 10.6 times a year, yet the book was re-trued ~80 times a year, ~70 of
        # them for price drift alone -- a 3x leg beside its 1x base leaves a 1%
        # band most days. 162 "trades a year" were the simulator's, not the
        # rule's. A sleeve the app tells you to trim at a 5-point band would
        # never trade like that.
        if want == last_traded and drift < drift_band:
            continue
        last_traded = dict(want)

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
                ep = entry_px.get(tk, px[tk][i])
                if px[tk][i] > ep:
                    wins += 1
                if ep:
                    trip_pnl.append((px[tk][i] / ep - 1.0) * 100.0)
                trip_days.append(i - entry_i.get(tk, i))
                shares.pop(tk, None)
                basis.pop(tk, None)
                entry_px.pop(tk, None)
                entry_i.pop(tk, None)

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
            entry_i.setdefault(tk, i)
            shares[tk] = prev_sh + buy_sh
            cash -= spend
            traded_notional += spend
            trades += 1

    return {"values": values, "tax_paid": tax_paid, "trades": trades,
            "traded_notional": traded_notional, "round_trips": closed, "wins": wins,
            "trip_pnl": trip_pnl, "trip_days": trip_days}


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
    # Per-trade view: expectancy is the average round trip, profit factor the
    # ratio of gross wins to gross losses. Both survive a strategy being out of
    # the market most of the time, which CAGR does not.
    pnl = sim.get("trip_pnl") or []
    if pnl:
        w = [x for x in pnl if x > 0]
        losses = [x for x in pnl if x <= 0]
        gross_loss = abs(sum(losses))
        out.update({
            "expectancy_pct_per_trade": round(float(np.mean(pnl)), 2),
            "avg_win_pct": round(float(np.mean(w)), 2) if w else None,
            "avg_loss_pct": round(float(np.mean(losses)), 2) if losses else None,
            "profit_factor": (round(sum(w) / gross_loss, 2) if gross_loss else None),
            "avg_holding_days": (round(float(np.mean(sim["trip_days"])), 1)
                                 if sim.get("trip_days") else None),
        })
    if bench is not None and bench.size:
        # THE BOOK BENCHMARK (settings.benchmark_ticker, normally SPY).
        # Answers "did this beat the market", which is a claim about the whole
        # portfolio. It is NOT a claim about whether the rule works -- see
        # base_series() and excess_over_base_cagr_pct below.
        b = cagr(list(bench)) * 100
        out["benchmark_cagr_pct"] = round(b, 2)
        out["excess_cagr_pct"] = round(out["cagr_pct"] - b, 2)
        out["benchmark_max_drawdown_pct"] = round(max_drawdown(list(bench)) * 100, 2)
    return out


def base_series(px: dict[str, np.ndarray], base: dict[str, float] | None) -> np.ndarray | None:
    """Buy-and-hold index of the strategy's OWN base, on the same dates.

    A trend rule on TQQQ levers QQQ; a trend rule on SOXL levers SMH. Measuring
    either against SPY makes most of its apparent excess a Nasdaq-or-semis factor
    bet rather than evidence the timing rule works. The catalog already names the
    right comparison in `base` -- this turns it into a series.

    Weights are fixed at the first session and never rebalanced, which is what
    "hold the thing you are levering" actually means.
    """
    if not base:
        return None
    if any(tk not in px or px[tk].size == 0 or px[tk][0] == 0 for tk in base):
        return None
    legs = [w * (px[tk] / px[tk][0]) for tk, w in base.items()]
    return np.sum(legs, axis=0)


def _base_metrics(out: dict, px: dict[str, np.ndarray], spec: dict) -> None:
    """Add the base-relative figures in place. Absent base -> nothing added.

    Deliberately separate keys from the benchmark ones. The two answer different
    questions and a book can easily have a positive `excess_over_base_cagr_pct`
    and a negative `excess_cagr_pct` at the same time -- the rule working while
    the book still loses to the market. Collapsing them into one number is how
    that goes unnoticed.
    """
    vals = base_series(px, spec.get("base"))
    if vals is None:
        out["base_tickers"] = None
        return
    b = cagr(list(vals)) * 100
    out["base_tickers"] = sorted(spec["base"])
    out["base_cagr_pct"] = round(b, 2)
    out["base_max_drawdown_pct"] = round(max_drawdown(list(vals)) * 100, 2)
    out["excess_over_base_cagr_pct"] = round(out["cagr_pct"] - b, 2)


# --------------------------------------------------------------------------
# public entry points
# --------------------------------------------------------------------------

def _exposure_stats(targets: list[dict], spec: dict) -> dict:
    """How much of the time the aggressive sleeve was actually deployed.

    A 4%/yr strategy that is in its instrument 12% of the time is a different
    animal from a 4%/yr strategy that is always in. The first has a sizing
    problem; the second has an edge problem.
    """
    sleeve = set(spec.get("weights") or {})
    base = set(spec.get("base") or {})
    aggressive = sleeve - base
    if not aggressive or not targets:
        return {}
    active = sum(1 for t in targets if any(t.get(tk, 0.0) > 0.005 for tk in aggressive))
    exposure = [sum(t.get(tk, 0.0) for tk in aggressive) for t in targets]
    return {
        "time_in_market_pct": round(100.0 * active / len(targets), 1),
        "avg_sleeve_exposure_pct": round(100.0 * float(np.mean(exposure)), 1),
        "base_when_flat": sorted(base) or None,
    }


def tickers_needed(spec: dict, *, regime_gate: bool = False) -> list[str]:
    """Every symbol the spec references, so the caller knows what to fetch.

    With ``regime_gate`` the regime proxy's own indexes are included. They must
    be fetched or the run abstains -- silently skipping the gate would produce a
    number labelled "gated" that was not.
    """
    o = spec.get("overlay") or {}
    names = set(spec.get("weights") or {}) | set(spec.get("base") or {})
    for key in ("gate_ticker", "signal_ticker", "risk_off"):
        if o.get(key):
            names.add(o[key])
    names.update(o.get("universe") or [])
    names.update(o.get("fallback") or [])
    legs = o.get("legs") or {}
    if o.get("kind") == "vol_managed":
        names.update(legs.values())
        if o.get("signal_ticker"):
            names.add(o["signal_ticker"])
    else:
        for fund, idx in legs.items():
            names.update((fund, idx))
    tg = o.get("trend_gate") or {}
    if tg.get("ticker"):
        names.add(tg["ticker"])
        names.update(tg.get("off") or {})
    if o.get("cash"):
        names.add(o["cash"])
    names.update((o.get("hold_map") or {}).values())
    if (o.get("market_gate") or {}).get("ticker"):
        names.add(o["market_gate"]["ticker"])
    for etf, stocks in ((o.get("two_stage") or {}).get("sectors") or {}).items():
        names.add(etf)
        names.update(stocks)
    if spec.get("risk_off"):
        names.add(spec["risk_off"])
    if regime_gate:
        names.update(rg.tickers_needed())
    return sorted(names)


def _apply_regime_gate(targets: list[dict], allow, spec: dict) -> list[dict]:
    """On days the regime blocks, hold what the strategy holds when it is flat.

    Applied AFTER the overlay has decided its targets, so it composes with every
    overlay kind rather than each one needing to know about regimes. The fallback
    is the same one the overlay itself uses when its setup is not live: the core
    holding, else the declared risk-off instrument, else nothing.
    """
    off = dict(spec.get("base") or {})
    if not off and spec.get("risk_off"):
        off = {spec["risk_off"]: 1.0}
    return [dict(t) if bool(a) else dict(off) for t, a in zip(targets, allow)]


def gate_verdict(ungated: dict, gated: dict, *,
                 max_giveup_pct: float | None = None) -> dict:
    """Did the regime gate earn its place on this strategy?

    Criterion (decided, not discovered): a shallower max drawdown at no worse
    than ``max_giveup_pct`` of annual return. A regime filter almost always
    trades a little CAGR for a lot less drawdown -- that trade IS the product, so
    judging on CAGR alone would reject a gate doing its job. Judging on drawdown
    alone would bless one that simply refuses to invest.

    **THE LIMIT IS NOW AN ARGUMENT, because it was never a measurement.** The
    module comment has always said so -- "a filter that costs more than a point
    a year is buying that shelter too dearly" is a preference about money, and
    1.0 is where somebody put it. On this catalog it is also decisive:
    `vol_target` buys 8.32 points of drawdown for 1.71%/yr and fails ONLY
    because 1.71 > 1.0. STATUS.md:2931 already records that "on a 3x leveraged
    sleeve, 8 points of drawdown for under 2%/yr is arguably a trade worth
    taking... whether vol_target should be switched on is Eran's call."

    A number that decides a verdict and belongs to the user has to be settable
    by the user. `None` keeps the historical default, so no existing caller
    changes behaviour.

    Returns the verdict AND both deltas, so the card can show the trade rather
    than only the conclusion -- plus, when the gate failed on cost alone, the
    limit at which it would pass. "It failed" and "it would pass at 1.8" are the
    same measurement, and only the second is a decision the reader can make.
    """
    limit = MAX_CAGR_GIVEUP_PCT if max_giveup_pct is None else float(max_giveup_pct)
    if not (ungated.get("ok") and gated.get("ok")):
        return {"comparable": False,
                "reason": "one of the two runs abstained, so they cannot be compared"}
    d_cagr = round(gated["cagr_pct"] - ungated["cagr_pct"], 2)
    d_dd = round(gated["max_drawdown_pct"] - ungated["max_drawdown_pct"], 2)
    shallower = d_dd < 0
    affordable = d_cagr >= -limit
    improves = bool(shallower and affordable)
    # Only meaningful when the gate DID reduce drawdown and lost on cost. A gate
    # that failed on drawdown cannot be rescued by any limit, and offering one
    # would invite raising the limit until something passes.
    flips_at = (round(abs(d_cagr), 2) if (shallower and not affordable) else None)
    if improves:
        why = (f"drawdown {abs(d_dd):.1f} points shallower for "
               f"{('+' if d_cagr >= 0 else '')}{d_cagr:.1f}%/yr")
    elif not shallower:
        why = f"drawdown was not reduced ({d_dd:+.1f} points)"
    else:
        why = (f"drawdown {abs(d_dd):.1f} points shallower, but it cost "
               f"{abs(d_cagr):.1f}%/yr -- more than the {limit:g}%/yr limit")
    return {"comparable": True, "improves": improves, "why": why,
            "cagr_delta_pct": d_cagr, "max_drawdown_delta_pct": d_dd,
            "cagr_giveup_limit_pct": limit,
            # Whether the number is still the one this module shipped with, NOT
            # whether the caller happened to pass it. `measure()` always passes
            # the setting, so `is None` would have read False even when nobody
            # had changed anything -- and "the user chose this limit" is exactly
            # the claim a reader needs to be able to check.
            "limit_is_default": abs(limit - MAX_CAGR_GIVEUP_PCT) < 1e-9,
            "would_improve_at_limit_pct": flips_at,
            "flip_note": (
                f"it would pass at a limit of {flips_at:g}%/yr or higher. That is "
                f"a decision about what you are willing to pay for "
                f"{abs(d_dd):.1f} points of drawdown, not a further measurement."
                if flips_at is not None else None),
            "observations_match": ungated.get("observations") == gated.get("observations")}


def run(series: dict[str, list[tuple[str, float]]], spec: dict, *,
        benchmark: list[tuple[str, float]] | None = None,
        cgt_rate: float = CGT_RATE, cost_bps: float = DEFAULT_COST_BPS,
        regime_gate: bool = False) -> dict:
    """Backtest one spec. Returns measured metrics, or abstains with a reason."""
    missing = [tk for tk in tickers_needed(spec, regime_gate=regime_gate) if tk not in series]
    if missing:
        return _abstain(MISSING_TICKER, f"no price history for {', '.join(missing)}")

    # PR 126: a one-day print that leaves and comes straight back is a data
    # error, not a market (`clean_spikes`). Counted, so a row says it happened.
    feed, bad_prints = {}, {}
    for tk, rows in series.items():
        feed[tk], k = clean_spikes(list(rows))
        if k:
            bad_prints[tk] = k
    if benchmark:
        feed["__bench__"] = benchmark
    # Which ticker's own inception date caps the window. Without this a short
    # span is indistinguishable from a truncated feed -- and those need opposite
    # responses: a young fund is fine, a truncated feed is a bug. AVUV listing in
    # 2019 legitimately caps its basket at ~7y; the Yahoo ladder silently
    # returning 5y for a decade request was a defect.
    late = late_tickers(spec)
    first_seen = {tk: min(d for d, _ in rows) for tk, rows in feed.items()
                  if rows and tk not in late}
    dates, px = align(feed, late=late)
    if not dates:
        return _abstain(NO_OVERLAP, "the tickers share no common trading dates")
    if len(dates) < _MIN_OBSERVATIONS:
        return _abstain(INSUFFICIENT_HISTORY,
                        f"{len(dates)} overlapping sessions; need {_MIN_OBSERVATIONS}")
    bench = px.pop("__bench__", None)

    targets = targets_for(px, spec)
    if isinstance(targets, dict):        # an abstention leaked through
        return targets
    # PR 126: a strategy with no base holds NOTHING until its score has the
    # history to be computed (252 sessions for a 1-year volatility). That is
    # "not able to run yet", not "chose cash", and scoring it as cash drags the
    # headline for a reason that is not the rule's. Measure from the first day
    # the rule holds anything. A strategy with a base is never empty: no change.
    first = next((k for k, t in enumerate(targets) if t), 0)
    if first > 0:
        dates, targets = dates[first:], targets[first:]
        px = {tk: a[first:] for tk, a in px.items()}
        if bench is not None:
            bench = bench[first:]
        if len(dates) < _MIN_OBSERVATIONS:
            return _abstain(INSUFFICIENT_HISTORY,
                            f"{len(dates)} sessions after warm-up; need {_MIN_OBSERVATIONS}")
    if regime_gate:
        # The SAME function the live read calls -- see app/engines/regime.py. If
        # these two ever diverge the card's numbers stop describing what runs.
        targets = _apply_regime_gate(targets, rg.gate(px), spec)

    band = float(spec.get("drift_band", 0.01))
    sim = _simulate(px, targets, cost_bps=cost_bps, cgt_rate=cgt_rate, drift_band=band)
    out = _metrics(sim, dates, bench)
    # Two benchmarks, never conflated: the book's (above, in _metrics) and the
    # strategy's own base (here). "Does the rule work?" and "does the book beat
    # the market?" are different questions with different answers.
    _base_metrics(out, px, spec)
    out.update(_exposure_stats(targets, spec))
    if bad_prints:
        out["bad_prints_dropped"] = bad_prints
    o = spec.get("overlay") or {}
    if o.get("kind") == "rank_select":
        # SELECTION EDGE: the ranking against holding every candidate equally on
        # the same cadence. The universe is today's list -- companies that
        # survived and grew -- so the headline flatters any rule run over it;
        # the edge differences that flattery out, and it is what the gate reads.
        own = own_hold_spec(spec)
        eq = _simulate(px, targets_for(px, own), cost_bps=cost_bps, cgt_rate=cgt_rate,
                       drift_band=band)
        eq_cagr = cagr(eq["values"]) * 100
        out["equal_hold_cagr_pct"] = round(eq_cagr, 2)
        out["selection_edge_pct"] = round(out["cagr_pct"] - eq_cagr, 2)
        out["universe_size"] = len(late & set(px))
        out["survivorship"] = ("measured over today's list of names, which survived to be "
                               "on it; read the selection edge, not the headline")

    starts = {k: v for k, v in first_seen.items() if k != "__bench__"}
    earliest = min(starts.values()) if starts else None
    # Only a ticker that starts materially LATER than the rest is limiting. When
    # every series begins on the same day they are all simply hitting the
    # provider's 10-year window, and naming one of them would invent a cause.
    late = {tk: d for tk, d in starts.items() if earliest and d > earliest}
    out["history_start_by_ticker"] = starts
    out["limiting_ticker"] = max(late, key=late.get) if late else None
    out["history_capped_by_provider"] = not late
    # Sessions per year. Real daily data lands near 252; a feed quietly serving
    # monthly bars (Yahoo does this for range=max) lands near 12, and would
    # otherwise pass as a long history.
    out["sessions_per_year"] = (round(out["observations"] / out["years"], 0)
                                if out.get("years") else None)

    # Tax drag is the honest headline for a high-turnover strategy: the same
    # path re-run tax-free, differenced. Buy-and-hold defers CGT indefinitely;
    # a swing system pays it as it goes, and that gap compounds.
    gross = _simulate(px, targets, cost_bps=cost_bps, cgt_rate=0.0, drift_band=band)
    gross_cagr = cagr(gross["values"]) * 100
    out.update({
        "ok": True,
        "gross_cagr_pct": round(gross_cagr, 2),
        "tax_drag_pct": round(gross_cagr - out["cagr_pct"], 2),
        "tax_paid_pct_of_start": round(sim["tax_paid"], 2),
        "cgt_rate_pct": round(cgt_rate * 100, 1),
        "cost_bps": cost_bps,
        "overlay": (spec.get("overlay") or {}).get("kind", "buy_hold"),
        "regime_gated": bool(regime_gate),
        "known_failure": MEASURED_FAILURES.get(
            (spec.get("overlay") or {}).get("kind", "buy_hold")),
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
    verdict_bench, decay, bench_decay = None, None, None
    if ins.get("ok") and oos.get("ok"):
        decay = ins["cagr_pct"] - oos["cagr_pct"]
        # Measured against the BENCHMARK's decay over the same split, not
        # against zero. Splitting at 2022 puts a bear market entirely in the
        # test half, so every strategy -- including buy-and-hold -- scores worse
        # after it. Judging raw decay called honest strategies overfitted for
        # the crime of living through the same market as everything else.
        if ins.get("benchmark_cagr_pct") is not None and oos.get("benchmark_cagr_pct") is not None:
            bench_decay = ins["benchmark_cagr_pct"] - oos["benchmark_cagr_pct"]
            relative = decay - bench_decay
        else:
            relative = decay
        verdict_bench = ("holds up" if relative <= 3 else
                         "weaker out of sample" if relative <= 12 else "likely overfitted")
    # PR 126 (gate test 3, approved 2026-10-13): the verdict is judged against
    # holding the strategy's OWN instruments over the same split. Against SPY,
    # simply holding TQQQ -- no parameters at all -- decays 53 points across
    # 2022 while SPY decays 6, so every 3x rule read "likely overfitted" for the
    # crime of being levered. The SPY-relative verdict is kept, labelled.
    own_decay, verdict = None, None
    if ins.get("ok") and oos.get("ok"):
        own = own_hold_spec(spec)
        oi = run({tk: _slice(r, hi=split_date) for tk, r in series.items()}, own, **kw)
        oo = run({tk: _slice(r, lo=split_date) for tk, r in series.items()}, own, **kw)
        if oi.get("ok") and oo.get("ok"):
            own_decay = oi["cagr_pct"] - oo["cagr_pct"]
            rel = decay - own_decay
            verdict = ("holds up" if rel <= 3 else
                       "weaker out of sample" if rel <= 12 else "likely overfitted")
        else:
            verdict = verdict_bench
    return {"split_date": split_date, "in_sample": ins, "out_of_sample": oos,
            "cagr_decay_pct": (round(decay, 2) if decay is not None else None),
            "benchmark_decay_pct": (round(bench_decay, 2) if bench_decay is not None else None),
            "own_hold_decay_pct": (round(own_decay, 2) if own_decay is not None else None),
            "verdict": verdict,
            "verdict_vs_benchmark": verdict_bench if decay is not None else None,
            "verdict_basis": ("decay relative to holding the strategy's own instruments over the same split"
                              if own_decay is not None else
                              "decay relative to the benchmark over the same split"
                              if bench_decay is not None else "raw decay (no benchmark)")}


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
