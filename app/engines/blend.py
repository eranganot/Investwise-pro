"""T1 - measure a core-plus-sleeves book by simulating it, not by averaging it.

The obvious implementation is wrong and it is worth saying why before the code.

**Wrong:** blend the headline numbers -- ``0.7 * core_cagr + 0.3 * sleeve_cagr``.
That is a weighted average of two CAGRs, which is not the CAGR of the blended
book: it ignores rebalancing and the order returns arrive in. For drawdown it is
worse than wrong. Averaging two max-drawdowns assumes the two never bottom on the
same day, and a trend-filtered TQQQ sleeve and an equity core bottom on *exactly*
the same days. The averaged figure understates the real drawdown by the largest
margin precisely when it matters most -- and the solver treats drawdown as a HARD
constraint, so an understated drawdown admits blends that should be rejected.

**Right:** blend the daily target vectors and run the existing simulator once.

    blended[t] = sum_i  w_i * component_targets_i[t]

fed to ``strategy_backtest._simulate``, produces a measured blended CAGR and a
measured blended max drawdown over real dates, with the true correlation between
core and sleeve baked in -- because it is the same simulated book on the same
days. No correlation parameter is invented, because none is needed.

Nothing here does I/O. The caller supplies price history; every function is a
pure transform, so this module is testable without a price provider and safe to
run under ``offload``.
"""
from __future__ import annotations

import numpy as np

from app.engines.performance import cagr, max_drawdown
from app.engines.strategy_backtest import (
    CGT_RATE,
    DEFAULT_COST_BPS,
    _MIN_OBSERVATIONS,
    _metrics,
    _simulate,
    align,
    targets_for,
)

# Reasons a blend refuses to produce a number. Surfaced verbatim, never rendered
# as a zero -- "not measurable" and "measured at zero" are different claims.
WEIGHTS_EXCEED_BOOK = "WEIGHTS_EXCEED_BOOK"
COMPONENT_MISALIGNED = "COMPONENT_MISALIGNED"
NO_COMPONENTS = "NO_COMPONENTS"
COMPONENT_ABSTAINED = "COMPONENT_ABSTAINED"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
NO_OVERLAP = "NO_OVERLAP"

_EPS = 1e-9
_MAX_EQUAL_RISK_LEVERAGE = 5.0


def _abstain(reason: str, detail: str = "", **extra) -> dict:
    return {"ok": False, "reason": reason, "detail": detail, **extra}


# --------------------------------------------------------------------------
# building components
# --------------------------------------------------------------------------

def core_spec(weights: dict[str, float]) -> dict:
    """The core as a buy-and-hold pseudo-strategy.

    The core has no rule -- it is whatever the sleeves have not claimed, held
    every day. Expressing it as a spec rather than as a special case means it
    goes through the same ``targets_for`` -> ``_simulate`` path as every sleeve,
    so a two-component blend cannot accidentally measure its two halves by two
    different methods.
    """
    total = sum(weights.values())
    if total <= 0:
        return {"id": "__core__", "weights": {}, "overlay": {"kind": "buy_hold"}}
    # Normalized to 1.0: the component's own weight decides its share of the
    # book, so its internal weights must describe composition only. Carrying
    # scale in both places is how a 20% sleeve becomes a 4% one.
    return {"id": "__core__",
            "weights": {tk: w / total for tk, w in weights.items()},
            "overlay": {"kind": "buy_hold"}}


def core_weights_from(held_ils: dict[str, float],
                      sleeve_tickers: set[str]) -> dict[str, float]:
    """Composition of the implicit core: what is held, minus what a sleeve claims.

    Pure on purpose -- the caller reads positions, this does the arithmetic.
    A ticker claimed by any sleeve is excluded entirely rather than split, which
    matches how ``all_sleeve_targets`` and the per-ticker drift cards already
    treat a shared ticker: the book holds ONE position per ticker and the sleeve
    owns it.
    """
    ex = {t.upper() for t in sleeve_tickers}
    return {tk.upper(): v for tk, v in held_ils.items()
            if tk.upper() not in ex and v > 0}


# --------------------------------------------------------------------------
# blending
# --------------------------------------------------------------------------

def blend_targets(component_targets: list[list[dict]],
                  weights: list[float]) -> list[dict]:
    """One target vector per session: the weighted sum of the components'.

    Three rules, each of which the app would otherwise violate:

    * **Same length, always.** Components must already share one date axis. A
      component whose history starts later is a hard error, not a silent
      left-pad -- a sleeve that did not exist for three years would otherwise
      look like a sleeve that was in cash, which flatters it.
    * **Weights may sum to less than 1.0.** The remainder is cash, explicitly.
      ``_simulate`` leaves unallocated value in cash on its own, so the
      objective's cash floor is modelled rather than implicitly redistributed.
    * **Two components wanting the same ticker SUM into one position.** The book
      holds one position per ticker; this is the same rule ``_arm_sleeve_caps``
      uses for ``max_weight`` and the same reason drift is measured per ticker
      at the summed target. Anything else models a book the app cannot hold.
    """
    if not component_targets:
        return []
    n = len(component_targets[0])
    out: list[dict] = []
    for i in range(n):
        row: dict[str, float] = {}
        for targets, w in zip(component_targets, weights):
            for tk, tw in targets[i].items():
                if tw:
                    row[tk] = row.get(tk, 0.0) + w * tw
        out.append(row)
    return out


def _equal_risk(values: list[float],
                target_dd: float) -> tuple[float, list[float]] | None:
    """Scale the blend's daily returns until its drawdown matches the benchmark's.

    This is the leverage-proof scoreboard. Raw excess CAGR is trivially bought
    with leverage -- anyone beats SPY by holding 1.3x SPY -- so a solver that
    maximizes it returns the most leveraged admissible blend every time. Scaling
    both sides to one risk level removes that, because leverage scales the
    numerator and the denominator together.

    Solved by bisection rather than by the closed form ``k = target/actual``,
    because drawdown is not linear in exposure and the closed form misses. The
    scaled path assumes the un-deployed part earns nothing and financing is
    free, which flatters leverage slightly -- stated rather than hidden, and the
    achieved drawdown is returned so the reader can see how close it landed.
    """
    v = np.asarray(values, dtype=float)
    if v.size < 3 or target_dd <= 0:
        return None
    rets = np.diff(v) / np.where(v[:-1] == 0, 1.0, v[:-1])
    if not np.isfinite(rets).all():
        return None

    def path(k: float) -> np.ndarray:
        return np.concatenate([[1.0], np.cumprod(1.0 + k * rets)])

    if max_drawdown(list(path(_MAX_EQUAL_RISK_LEVERAGE))) < target_dd:
        return None                      # even at the cap it is safer; no answer
    lo, hi = 0.0, _MAX_EQUAL_RISK_LEVERAGE
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if max_drawdown(list(path(mid))) < target_dd:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    return k, list(path(k))


def measure_blend(series: dict[str, list[tuple[str, float]]],
                  components: list[dict], *,
                  benchmark: list[tuple[str, float]] | None = None,
                  cgt_rate: float = CGT_RATE,
                  cost_bps: float = DEFAULT_COST_BPS) -> dict:
    """Measure a blended book. Same metrics shape as a single strategy.

    ``components`` is ``[{"id": str, "spec": dict, "weight": float}]`` where
    ``weight`` is a FRACTION OF NAV in 0..1 -- not a percentage. Returning the
    standard metrics dict means every renderer that can already draw a strategy
    card can draw a blend, plus four fields a blend needs and a single strategy
    does not.
    """
    if not components:
        return _abstain(NO_COMPONENTS, "a blend needs at least one component")

    weights = [float(c.get("weight") or 0.0) for c in components]
    if any(w < 0 for w in weights):
        return _abstain(WEIGHTS_EXCEED_BOOK, "a component weight is negative")
    total_w = sum(weights)
    if total_w > 1.0 + _EPS:
        return _abstain(WEIGHTS_EXCEED_BOOK,
                        f"components sum to {total_w * 100:.1f}% of NAV")

    feed = dict(series)
    if benchmark:
        feed["__bench__"] = benchmark
    dates, px = align(feed)
    if not dates:
        return _abstain(NO_OVERLAP, "the components share no common trading dates")
    if len(dates) < _MIN_OBSERVATIONS:
        return _abstain(INSUFFICIENT_HISTORY,
                        f"{len(dates)} overlapping sessions; need {_MIN_OBSERVATIONS}")
    bench = px.pop("__bench__", None)

    # Targets per component, on the ONE date axis align() just produced.
    per_component: list[list[dict]] = []
    for c in components:
        t = targets_for(px, c["spec"])
        if isinstance(t, dict):          # an abstention leaked through
            return _abstain(COMPONENT_ABSTAINED,
                            f"{c['id']}: {t.get('reason')} {t.get('detail')}".strip(),
                            component=c["id"])
        if len(t) != len(dates):
            return _abstain(COMPONENT_MISALIGNED,
                            f"{c['id']} produced {len(t)} sessions, "
                            f"expected {len(dates)}",
                            component=c["id"])
        per_component.append(t)

    blended = blend_targets(per_component, weights)
    sim = _simulate(px, blended, cost_bps=cost_bps, cgt_rate=cgt_rate)
    out = _metrics(sim, dates, bench)
    out["ok"] = True
    out["components"] = []
    out["allocated_pct"] = round(total_w * 100, 2)
    out["cash_pct"] = round(max(0.0, 1.0 - total_w) * 100, 2)

    # Each component measured ALONE over the same window, at full weight. This
    # is what makes diversification_delta_pct a measurement rather than a claim.
    weighted_dd = 0.0
    for c, t, w in zip(components, per_component, weights):
        solo = _simulate(px, t, cost_bps=cost_bps, cgt_rate=cgt_rate)
        c_cagr = round(cagr(solo["values"]) * 100, 2)
        c_dd = round(max_drawdown(solo["values"]) * 100, 2)
        weighted_dd += w * c_dd
        out["components"].append({
            "id": c["id"], "weight_pct": round(w * 100, 2),
            "cagr_pct": c_cagr, "max_drawdown_pct": c_dd,
        })

    # Near zero is the EXPECTED answer for a leveraged sleeve on an equity core:
    # they fall together. Printing it proves that rather than asserting it, and
    # a materially negative number is the only evidence that the blend is
    # actually diversifying anything.
    out["diversification_delta_pct"] = round(
        out["max_drawdown_pct"] - weighted_dd, 2)

    # What the unallocated remainder costs. The cash floor is real money and its
    # price should be visible, not buried in a lower CAGR nobody can attribute.
    if total_w < 1.0 - _EPS and total_w > 0:
        full = blend_targets(per_component, [w / total_w for w in weights])
        fs = _simulate(px, full, cost_bps=cost_bps, cgt_rate=cgt_rate)
        out["cash_drag_pct"] = round(cagr(fs["values"]) * 100 - out["cagr_pct"], 2)
    else:
        out["cash_drag_pct"] = 0.0

    # The leverage-proof verdict. Only computable against a benchmark, because
    # "equal risk" means equal to something.
    out["excess_at_equal_risk_pct"] = None
    out["equal_risk_leverage"] = None
    if bench is not None and bench.size:
        bench_dd = max_drawdown(list(bench))
        scaled = _equal_risk(sim["values"], bench_dd)
        if scaled is not None:
            k, path = scaled
            out["equal_risk_leverage"] = round(k, 3)
            out["equal_risk_drawdown_pct"] = round(max_drawdown(path) * 100, 2)
            out["excess_at_equal_risk_pct"] = round(
                cagr(path) * 100 - cagr(list(bench)) * 100, 2)

    out["blend_engine"] = "b1"
    return out
