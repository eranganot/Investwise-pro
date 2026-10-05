"""PR 89 - what the frontier is fed: ILS returns, a shrunk covariance, and a
Black-Litterman expected return.

PR 88 shipped the optimiser and said nothing about its inputs, which is where
mean-variance actually goes wrong: it maximises whatever error is in mu, and
inverts whatever noise is in the covariance. This module is the one place those
two numbers are built, and every one of them carries where it came from.

**THE UNIVERSE** (Eran, 2026-10-05: asset classes plus a US/intl split, not
tickers). Each class is a proxy basket; each SLEEVE is its strategy's simulated
daily return stream -- not its ticker's. `btm_swing_dip` is out of TQQQ some of
the time, and `portfolio_risk.py:187` records what happens when a rule's
volatility is read off the instrument it trades: the figure describes something
the book does not run.

**ILS, NOT USD.** Every proxy and sleeve instrument is priced in dollars and the
book is managed in shekels. A USD return converted at a constant spot rate is
still a USD return -- the constant cancels in every ratio, which is exactly the
caveat `performance_service` carries. So the daily USD/ILS change is compounded
into every risky asset: (1 + r_usd)(1 + r_fx) - 1. When the FX history cannot be
fetched the matrix falls back to USD returns and `fx_basis` says "spot";
nothing pretends the currency risk was measured.

**CASH IS THE RISK-FREE ASSET, IN SHEKELS.** Zero variance in ILS, earning the
rate the instrument table already states for cash. It is appended to the
covariance as a zero row after every inversion is done -- a zero row inside the
Black-Litterman algebra would make it singular.

**THE PRIOR: what the plan's own target implies.** Black-Litterman starts from
equilibrium returns, pi = rf + lambda S w_ref. The textbook reference is
market-cap weights; here it is the plan's FIXED target (`OBJ_TARGET`) because
that is the allocation the plan already stands behind, so with no views the
optimiser hands back the target the book already has, and every tilt away from
it is attributable to a view. lambda is calibrated so the reference earns the
premium `strategy_profile`'s instrument table already states for it -- no new
return figure is introduced here, only the covariance-weighted SPREAD of the
one that exists.

**THE VIEWS: only what was measured.** One absolute view per sleeve whose
out-of-sample measurement is usable, at its OOS CAGR (converted through
`return_convention`). Its uncertainty is the sampling variance of a mean over
the OOS window -- variance / years -- so a view resting on 3.8 years of data
moves the answer less than one on ten, and no confidence knob exists to tune.
A sleeve with no usable measurement gets no view and the payload says why.

Pure core (`build_inputs`) plus one async loader. Read-only.
"""
from __future__ import annotations

import logging
import math

import numpy as np

from app.engines import frontier as fr
from app.engines import strategy_backtest as bt

logger = logging.getLogger(__name__)

INPUTS_VERSION = "i1"

# Yahoo's USD/ILS: shekels per dollar. A rise is a dollar asset gaining in ILS.
FX_TICKER = "ILS=X"

CASH_ID = "cash"

# The class proxies. `assumption_ticker` names the instrument-table row whose
# stated return feeds the PRIOR's calibration (never the cross-section).
CLASS_PROXIES: dict[str, dict] = {
    "us_equity": {"label": "US Equities", "book_class": "Equities",
                  "weights": {"VTI": 1.0}, "assumption_ticker": "VTI"},
    "intl_equity": {"label": "Intl Equities", "book_class": "Equities",
                    "weights": {"VXUS": 1.0}, "assumption_ticker": "VXUS"},
    "fixed_income": {"label": "Fixed Income", "book_class": "Fixed Income",
                     "weights": {"BND": 1.0}, "assumption_ticker": "BND"},
    # Gold and a broad basket, the split the All-Weather core already runs.
    "commodities": {"label": "Commodities", "book_class": "Commodities",
                    "weights": {"IAU": 0.6, "DBC": 0.4}, "assumption_ticker": "IAU"},
}

# How a book-level Equities share is divided for the reference. Roughly the
# non-US share of global equity market capitalisation; the only number in this
# module that is not read from elsewhere in the app, and it shapes only the
# prior -- a view on either half overrides it.
REFERENCE_INTL_SHARE = 0.4

# Black-Litterman's tau: how uncertain the prior is relative to the covariance.
# 0.05 is the conventional value (He & Litterman use 0.025-0.05).
TAU = 0.05

# Why a sleeve has no view. Named, never silently dropped.
VIEW_NOT_MEASURED = "NOT_MEASURED"
VIEW_FAILED = "MEASUREMENT_FAILED"
VIEW_STALE = "ENGINE_VERSION_MOVED"
VIEW_NO_OOS = "NO_OUT_OF_SAMPLE"
VIEW_SHORT = "TOO_FEW_OBSERVATIONS"

# Why the whole payload is degraded.
DEGRADED_FX_SPOT = "FX_SPOT"            # no USD/ILS history; returns are in USD
DEGRADED_NO_PREMIUM = "NO_PREMIUM"      # reference earns <= cash; prior is flat


# --------------------------------------------------------------------------
# pure
# --------------------------------------------------------------------------

def reference_mix(objective_target: dict[str, float]) -> dict[str, float]:
    """The plan's class target -> weights over this module's asset ids."""
    eq = float(objective_target.get("Equities") or 0.0)
    ref = {
        "us_equity": eq * (1.0 - REFERENCE_INTL_SHARE),
        "intl_equity": eq * REFERENCE_INTL_SHARE,
        "fixed_income": float(objective_target.get("Fixed Income") or 0.0),
        "commodities": float(objective_target.get("Commodities") or 0.0),
        CASH_ID: float(objective_target.get("Cash") or 0.0),
    }
    total = sum(ref.values())
    return {k: v / total for k, v in ref.items()} if total > 0 else ref


def _returns(a: np.ndarray) -> np.ndarray:
    return a[1:] / a[:-1] - 1.0


def _basket_returns(px: dict[str, np.ndarray], weights: dict[str, float]) -> np.ndarray:
    """Daily-rebalanced basket: the weighted sum of constituent returns."""
    total = sum(weights.values())
    return sum((w / total) * _returns(px[tk]) for tk, w in weights.items())


def _sleeve_returns(px: dict[str, np.ndarray], spec: dict) -> np.ndarray | None:
    """The RULE's daily returns, pre-tax, on the shared calendar.

    Pre-tax on purpose: the simulator pays CGT in lumps on the day of a sale,
    which is a cash-flow artefact, not daily risk. The tax drag lives in the
    measured CAGR the view is built from.
    """
    targets = bt.targets_for(px, spec)
    if isinstance(targets, dict):
        return None
    sim = bt._simulate(px, targets, cost_bps=bt.DEFAULT_COST_BPS, cgt_rate=0.0)
    values = np.asarray(sim["values"], dtype=float)
    if values.size < 2 or np.any(values <= 0):
        return None
    return _returns(values)


def view_from_measurement(payload: dict | None, *, engine_version: str,
                          min_observations: int) -> tuple[dict | None, str | None]:
    """A stored backtest payload -> (OOS measurement, None) or (None, reason)."""
    if not payload:
        return None, VIEW_NOT_MEASURED
    if not payload.get("ok"):
        return None, VIEW_FAILED
    if (payload.get("engine_version") or "") != engine_version:
        return None, VIEW_STALE
    oos = ((payload.get("robustness") or {}).get("out_of_sample") or {})
    inner = oos.get("out_of_sample") if isinstance(oos.get("out_of_sample"), dict) else oos
    if not inner or inner.get("cagr_pct") is None or inner.get("volatility_pct") is None:
        return None, VIEW_NO_OOS
    obs = int(inner.get("observations") or 0)
    if obs < min_observations:
        return None, VIEW_SHORT
    return {"cagr_pct": float(inner["cagr_pct"]),
            "volatility_pct": float(inner["volatility_pct"]),
            "observations": obs, "start": inner.get("start"), "end": inner.get("end")}, None


def black_litterman(cov: np.ndarray, prior: np.ndarray, p: np.ndarray, q: np.ndarray,
                    omega: np.ndarray, *, tau: float = TAU) -> np.ndarray:
    """Posterior mean: [(tS)^-1 + P'O^-1 P]^-1 [(tS)^-1 pi + P'O^-1 q]."""
    if p.size == 0:
        return prior.copy()
    ts_inv = np.linalg.inv(tau * cov)
    o_inv = np.linalg.inv(omega)
    a = ts_inv + p.T @ o_inv @ p
    b = ts_inv @ prior + p.T @ o_inv @ q
    return np.linalg.solve(a, b)


def build_inputs(series: dict[str, list[tuple[str, float]]], *, sleeves: list[dict],
                 objective_target: dict[str, float], measurements: dict[str, tuple],
                 class_assumptions: dict[str, tuple[float, float]],
                 cash_rate_pct: float, tau: float = TAU) -> dict:
    """Price history in, frontier inputs out. Pure.

    ``sleeves`` is ``[{"id", "spec", "current_pct"}]`` (the solvers' shape);
    ``measurements`` maps sleeve id -> ``view_from_measurement(...)``'s pair;
    ``class_assumptions`` maps class id -> the instrument table's
    ``(cagr_pct, volatility_pct)``.
    """
    degraded: list[str] = []
    excluded: list[dict] = []

    feed = {tk: rows for tk, rows in series.items() if tk != FX_TICKER}
    fx_rows = series.get(FX_TICKER)
    if fx_rows:
        feed[FX_TICKER] = fx_rows
    else:
        degraded.append(DEGRADED_FX_SPOT)

    # Assets whose tickers never arrived are excluded BY NAME, before aligning,
    # so one missing symbol cannot shrink the shared calendar to nothing.
    have = set(feed)
    classes = []
    for cid, c in CLASS_PROXIES.items():
        missing = sorted(set(c["weights"]) - have)
        if missing:
            excluded.append({"id": cid, "reason": "NO_PRICE_HISTORY", "tickers": missing})
        else:
            classes.append(cid)
    live_sleeves = []
    for s in sleeves:
        missing = sorted(set(bt.tickers_needed(s["spec"])) - have)
        if missing:
            excluded.append({"id": s["id"], "reason": "NO_PRICE_HISTORY", "tickers": missing})
        else:
            live_sleeves.append(s)
    needed = set().union(*[set(CLASS_PROXIES[c]["weights"]) for c in classes]) if classes else set()
    for s in live_sleeves:
        needed |= set(bt.tickers_needed(s["spec"]))
    if fx_rows:
        needed.add(FX_TICKER)
    if not needed:
        return {"ok": False, "reason": "NO_ASSETS", "detail": "nothing could be priced",
                "excluded": excluded}

    dates, px = bt.align({tk: feed[tk] for tk in needed})
    if len(dates) < bt._MIN_OBSERVATIONS:
        return {"ok": False, "reason": "INSUFFICIENT_HISTORY",
                "detail": f"{len(dates)} shared sessions; need {bt._MIN_OBSERVATIONS}",
                "excluded": excluded}

    fx_r = _returns(px[FX_TICKER]) if fx_rows else np.zeros(len(dates) - 1)
    cols, assets = [], []
    for cid in classes:
        c = CLASS_PROXIES[cid]
        cols.append(_basket_returns(px, c["weights"]))
        assets.append({"id": cid, "kind": "class", "label": c["label"],
                       "book_class": c["book_class"], "proxy": c["weights"]})
    for s in live_sleeves:
        r = _sleeve_returns(px, s["spec"])
        if r is None:
            excluded.append({"id": s["id"], "reason": "SIMULATION_FAILED"})
            continue
        cols.append(r)
        assets.append({"id": s["id"], "kind": "sleeve", "label": s["id"],
                       "current_pct": s.get("current_pct")})
    if not cols:
        return {"ok": False, "reason": "NO_ASSETS", "detail": "nothing could be measured",
                "excluded": excluded}

    usd = np.column_stack(cols)
    ils = (1.0 + usd) * (1.0 + fx_r)[:, None] - 1.0
    cov, shrink = fr.ledoit_wolf(ils)
    vols = np.sqrt(np.diag(cov))
    n = len(assets)

    # ---- the prior -------------------------------------------------------
    rf = fr.arithmetic_drift(cash_rate_pct, 0.0)
    ref = reference_mix(objective_target)
    w_ref = np.array([ref.get(a["id"], 0.0) for a in assets])
    premium = 0.0
    for a, w in zip(assets, w_ref):
        if w > 0:
            cagr, vol = class_assumptions[a["id"]]
            premium += w * (fr.arithmetic_drift(cagr, vol) - rf)
    ref_var = float(w_ref @ cov @ w_ref)
    if premium > 0 and ref_var > 0:
        lam_ref = premium / ref_var
        prior = rf + lam_ref * (cov @ w_ref)
    else:
        degraded.append(DEGRADED_NO_PREMIUM)
        lam_ref = 0.0
        prior = np.full(n, rf)

    # ---- the views -------------------------------------------------------
    views, p_rows, q, omega = [], [], [], []
    for i, a in enumerate(assets):
        if a["kind"] != "sleeve":
            continue
        meas, why = measurements.get(a["id"], (None, VIEW_NOT_MEASURED))
        if meas is None:
            views.append({"asset": a["id"], "used": False, "reason": why})
            continue
        years = meas["observations"] / bt.TRADING_DAYS
        q_i = fr.arithmetic_drift(meas["cagr_pct"], meas["volatility_pct"])
        row = np.zeros(n)
        row[i] = 1.0
        p_rows.append(row)
        q.append(q_i)
        omega.append(float(cov[i, i]) / years)
        views.append({"asset": a["id"], "used": True, "cagr_pct": meas["cagr_pct"],
                      "drift": q_i, "observations": meas["observations"],
                      "years": round(years, 2), "window": [meas.get("start"), meas.get("end")],
                      "basis": "measured_oos"})
    p = np.array(p_rows) if p_rows else np.zeros((0, n))
    posterior = black_litterman(cov, prior, p, np.array(q), np.diag(omega) if omega else
                                np.zeros((0, 0)), tau=tau)

    # ---- cash, appended last ---------------------------------------------
    assets.append({"id": CASH_ID, "kind": "cash", "label": "Cash (ILS)"})
    cov_full = np.zeros((n + 1, n + 1))
    cov_full[:n, :n] = cov
    prior_full = np.append(prior, rf)
    post_full = np.append(posterior, rf)
    vols_full = np.append(vols, 0.0)

    for a, pr, po, v in zip(assets, prior_full, post_full, vols_full):
        a["prior_drift"] = round(float(pr), 6)
        a["drift"] = round(float(po), 6)
        a["volatility"] = round(float(v), 6)
        # The same figure as a CAGR, so the screen reads in the app's convention.
        a["cagr_pct"] = round(math.expm1(float(po) - 0.5 * float(v) ** 2) * 100, 2)

    return {
        "ok": True, "version": INPUTS_VERSION,
        "assets": assets,
        "mu": [round(float(x), 8) for x in post_full],
        "cov": [[round(float(x), 10) for x in row] for row in cov_full],
        "shrinkage": round(shrink, 4),
        "risk_free": {"drift": rf, "cagr_pct": cash_rate_pct,
                      "source": "instrument table, cash row (a planning assumption)"},
        "prior": {"basis": "reverse-optimised from the plan's fixed target",
                  "reference": {k: round(v, 4) for k, v in ref.items()},
                  "risk_aversion": round(lam_ref, 4), "tau": tau},
        "views": views,
        "fx_basis": "historical" if fx_rows else "spot",
        "window": {"start": dates[0], "end": dates[-1], "sessions": len(dates)},
        "excluded": excluded,
        "degraded": degraded,
    }


def as_arrays(payload: dict) -> tuple[list[str], np.ndarray, np.ndarray]:
    """(asset ids, mu, cov) from a payload -- what `frontier.optimize` takes."""
    return ([a["id"] for a in payload["assets"]],
            np.asarray(payload["mu"], dtype=float), np.asarray(payload["cov"], dtype=float))


# --------------------------------------------------------------------------
# the loader
# --------------------------------------------------------------------------

def _fetch_blocking(tickers: list[str]) -> tuple[dict, list[str], list[str]]:
    from app.services.backtest_service import _fetch
    return _fetch(sorted(tickers), classify=True)


async def frontier_inputs_for(session, user) -> dict:
    """The acting user's frontier inputs. Read-only; fetches ~10y of closes."""
    from app.core.offload import offload
    from app.services import backtest_service, target_solver
    from app.services.allocation_mix import OBJ_TARGET
    from app.services.measured_assumptions import MIN_OBSERVATIONS
    from app.services.plan_service import get_plan
    from app.services.strategy_profile import assumptions_for

    book, err = await target_solver._book_for_solve(session, user)
    if err and err.get("reason") != "NO_SLEEVES":
        return {"ok": False, **{k: v for k, v in err.items() if k != "execution_plan"}}
    sleeves = (book or {}).get("sleeves") or []
    plan = (book or {}).get("plan") or await get_plan(session, user)
    objective = getattr(plan, "objective", None) or "Balanced"
    objective_target = OBJ_TARGET.get(objective) or OBJ_TARGET["Balanced"]

    stored = await backtest_service.get_many(session, [s["id"] for s in sleeves]) if sleeves else {}
    measurements = {s["id"]: view_from_measurement(
        stored.get(s["id"]), engine_version=backtest_service.ENGINE_VERSION,
        min_observations=MIN_OBSERVATIONS) for s in sleeves}

    tickers = {FX_TICKER}
    for c in CLASS_PROXIES.values():
        tickers |= set(c["weights"])
    for s in sleeves:
        tickers |= set(bt.tickers_needed(s["spec"]))
    series, no_data, unavailable = await offload(_fetch_blocking, sorted(tickers))

    class_assumptions = {cid: assumptions_for(c["assumption_ticker"])
                         for cid, c in CLASS_PROXIES.items()}
    cash_rate_pct = assumptions_for("CASH", "cash")[0]
    out = build_inputs(series, sleeves=sleeves, objective_target=objective_target,
                       measurements=measurements, class_assumptions=class_assumptions,
                       cash_rate_pct=cash_rate_pct)
    out["objective"] = objective
    out["no_history"] = sorted(no_data)
    out["unavailable"] = sorted(unavailable)
    return out
