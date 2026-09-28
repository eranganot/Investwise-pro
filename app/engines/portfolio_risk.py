"""Phase D - PORTFOLIO-LEVEL RISK (returns-based, real correlations).

Unlike the per-position Monte Carlo in risk_engine, this looks at the book as a
whole using historical daily returns:

  * covariance / correlation across holdings -> portfolio volatility (annualized)
  * historical Value-at-Risk and Conditional VaR (expected shortfall)
  * real beta vs a benchmark (regression slope), not a hand-set number
  * goal-based Monte Carlo: probability of reaching a target wealth by a date,
    using the portfolio's own mu/sigma.

Pure math, no I/O - the service layer supplies the price history.
"""
from __future__ import annotations

import math

import numpy as np


def pct_returns(closes: list[float]) -> np.ndarray:
    a = np.asarray(closes, dtype=float)
    if a.size < 2:
        return np.array([])
    return a[1:] / a[:-1] - 1.0


def _align(series: list[np.ndarray]) -> np.ndarray:
    """Stack return series into a T x N matrix, aligned on the most recent L rows."""
    series = [s for s in series if s.size > 0]
    if not series:
        return np.empty((0, 0))
    L = min(s.size for s in series)
    return np.column_stack([s[-L:] for s in series])


def analyze(*, tickers: list[str], weights: list[float],
            history_by_ticker: dict[str, list[float]],
            benchmark_returns: np.ndarray | None = None,
            nav: float = 0.0, target: float | None = None, years: float | None = None,
            drift_pct: float | None = None, assumed_volatility_pct: float | None = None,
            runs: int = 10000, seed: int = 7, trading_days: int = 252) -> dict:
    """Portfolio risk from real returns, plus a goal projection IF one is stated.

    `drift_pct` is the STATED forward return, annualised, as a percentage. The
    goal projection is built from it and from nothing else.

    IT USED TO BE THE TRAILING MEAN, and that is the defect this argument
    exists to remove. Measured on production 2026-09-04: `port_rets.mean() *
    252` came to **107.08%/yr** on a 3x-leveraged NASDAQ and semiconductor book
    after a good year, and five years of that produced `prob_reach 0.9604`, a
    median terminal of ILS 1,407,623 and a p90 of ILS 9,601,434 -- on a book
    worth ILS 21,208. The UI renders the first of those as "Reach 102,973 in
    5y - 96%", which was the most confident number on the screen and the least
    supportable.

    `None` means no projection, deliberately. The old fallback WAS the trailing
    mean; keeping it as a fallback would preserve the defect and hide it better.
    A probability with no stated basis is worse than no probability, so the
    payload omits `goal` and says why in `goal_omitted_reason`.

    `assumed_volatility_pct` is the plan's blended assumption, carried through
    so the two numbers can be compared in the one place both exist. See
    `volatility_exceeds_assumption` below: the assumption is an upper bound, so
    a realised figure above it is a fact worth surfacing -- but the note names
    the candidate explanations rather than asserting one, because a first
    attempt at asserting one shipped wrong (PR 55.1, withdrawn).

    Trailing figures are still reported. `annualized_return_pct` keeps its name
    and value, and `trailing_return_pct` is the same number under a name that
    cannot be mistaken for a forecast: hiding it would trade one unstated basis
    for another.
    """
    w = np.asarray(weights, dtype=float)
    if w.sum() <= 0:
        w = np.ones(len(tickers))
    w = w / w.sum()

    rmat = _align([pct_returns(history_by_ticker.get(t, [])) for t in tickers])
    if rmat.size == 0 or rmat.shape[0] < 2:
        return {"ok": False, "reason": "insufficient price history"}

    n = rmat.shape[1]
    w = w[:n] if w.size >= n else np.pad(w, (0, n - w.size), constant_values=0)
    w = w / w.sum() if w.sum() > 0 else np.ones(n) / n

    cov_d = np.cov(rmat, rowvar=False)
    cov_d = np.atleast_2d(cov_d)
    cov_a = cov_d * trading_days
    port_vol = float(np.sqrt(max(0.0, w @ cov_a @ w)))

    corr = np.corrcoef(rmat, rowvar=False) if n > 1 else np.array([[1.0]])
    corr = np.atleast_2d(corr)
    off = corr[~np.eye(n, dtype=bool)]
    avg_corr = float(np.mean(off)) if off.size else 0.0

    port_rets = rmat @ w
    mu_a = float(port_rets.mean() * trading_days)
    p5 = np.percentile(port_rets, 5)
    var95 = float(-p5)
    tail = port_rets[port_rets <= p5]
    cvar95 = float(-tail.mean()) if tail.size else var95

    beta = None
    if benchmark_returns is not None and benchmark_returns.size > 1:
        L = min(port_rets.size, benchmark_returns.size)
        pr, br = port_rets[-L:], np.asarray(benchmark_returns[-L:], dtype=float)
        vb = br.var()
        if vb > 0 and L > 2:
            beta = float(np.cov(pr, br)[0, 1] / vb)

    goal = None
    goal_omitted_reason = None
    if not (target and years and nav > 0):
        goal_omitted_reason = "no target, horizon or NAV to project from"
    elif drift_pct is None:
        # The refusal, not an oversight. See the docstring: the fallback used to
        # be `mu_a`, and restoring it here is the whole bug.
        goal_omitted_reason = (
            "no stated forward return to project from. The trailing return is "
            "reported as `trailing_return_pct`, but a five-year probability "
            "built from one year of a leveraged book is not a forecast.")
    else:
        # THE CONVENTION, because it changes the answer by more than the fix
        # does. `drift_pct` is a CAGR: `measured_assumptions` builds each
        # component's return from `metrics["cagr_pct"]` and its caveat calls it
        # "a measured CAGR" in the user's own words. A CAGR is already a
        # GEOMETRIC growth rate -- the -0.5*sigma^2 volatility drag is baked
        # into it -- so it is compounded directly and the drag is NOT applied a
        # second time. The median is `nav * (1+g)^T` -- "compound at the
        # stated rate", which is what a reader means by 22%/yr (see below).
        #
        # Subtracting the drag anyway would have been the conservative-looking
        # mistake: on the live book, sigma is 67.5%, so 0.5*sigma^2 is 22.8% and
        # would have wiped out the entire 22.18% assumption, putting the median
        # five-year outcome BELOW today's NAV for arithmetic reasons rather than
        # for anything the measurement said.
        # PR 76 (review, CONFIRMED): `exp(g*T)` is CONTINUOUS compounding at g,
        # not "compound at the stated rate" -- 17% high over 6 years at 24.98%
        # (₪112,570 against ₪95,840 on Eran's book). The annual-compound rate g
        # is the continuous rate log1p(g); with that the median is exactly
        # nav*(1+g)^T, the same median `return_convention.drift_pct` gives the
        # plan projection.
        drift = math.log1p(max(float(drift_pct) / 100.0, -0.999999))
        rng = np.random.default_rng(seed)
        z = rng.standard_normal(runs)
        terminal = nav * np.exp(drift * years + port_vol * np.sqrt(years) * z)
        goal = {
            "target": round(float(target), 2), "years": round(float(years), 2),
            "prob_reach": round(float(np.mean(terminal >= target)), 4),
            "median_terminal": round(float(np.median(terminal)), 2),
            "p10_terminal": round(float(np.percentile(terminal, 10)), 2),
            "p90_terminal": round(float(np.percentile(terminal, 90)), 2),
            # On the card, so the reader can see what the probability was made of.
            "drift_pct": round(float(drift_pct), 2),
            "drift_source": "stated forward return",
            "drift_convention": "cagr",
            "volatility_pct": round(port_vol * 100, 2),
        }

    out = {
        "ok": True,
        "observations": int(rmat.shape[0]),
        "annualized_volatility_pct": round(port_vol * 100, 2),
        "annualized_return_pct": round(mu_a * 100, 2),
        # The same number under a name that cannot be read as a forecast.
        "trailing_return_pct": round(mu_a * 100, 2),
        "var_95_1d_pct": round(var95 * 100, 2),
        "cvar_95_1d_pct": round(cvar95 * 100, 2),
        "var_95_1d_ils": round(var95 * nav, 2) if nav else None,
        "avg_correlation": round(avg_corr, 3),
        "beta": round(beta, 3) if beta is not None else None,
        "goal": goal,
    }
    if goal_omitted_reason:
        out["goal_omitted_reason"] = goal_omitted_reason

    if assumed_volatility_pct is not None:
        # THE COMPARISON IS THE POINT, and it is sharper than "these disagree".
        # The plan's number is a NAV-weighted average of component volatilities,
        # and sqrt(w'Sw) <= sum(w.sigma) with equality only at perfect
        # correlation -- so it is an UPPER BOUND on the volatility of any
        # portfolio built from those components. A realised volatility above it
        # is not a difference of method; it means at least one component
        # understates what the book is actually doing.
        #
        # WHAT THE NOTE MAY AND MAY NOT SAY. The bound argument above is sound.
        # The reason the bound is breached is NOT established, and PR 55.1
        # shipped one explanation -- that the component volatilities are
        # diluted by days out of the market, correctable by sigma/sqrt(f) --
        # which measurement then refuted: the sleeves hold their BASE when the
        # rule is off, not cash, so out-of-market days run at 22.46%, not zero.
        # The two figures also cover different windows (ten-year strategy
        # backtests against 251 trading days here), which is a second candidate
        # nothing has separated from the first. So the note lists both and
        # asserts neither. A number the screen cannot explain is a smaller
        # problem than an explanation the screen invents.
        assumed = float(assumed_volatility_pct)
        out["assumed_volatility_pct"] = round(assumed, 2)
        out["volatility_exceeds_assumption"] = bool(port_vol * 100 > assumed)
        if out["volatility_exceeds_assumption"]:
            out["volatility_note"] = (
                f"Realised volatility {port_vol * 100:.2f}% is above the plan's "
                f"assumption of {assumed:.2f}%. That assumption is a NAV-weighted "
                f"average of component volatilities, which is an upper bound on "
                f"the volatility of any portfolio built from them, so the two "
                f"cannot both describe the same book over the same period. Two "
                f"things differ and neither has been ruled out: the assumption "
                f"is measured over each strategy's full backtest window while "
                f"this figure covers the last year, and the strategies' measured "
                f"volatilities average over periods when their rules held a "
                f"different instrument than the book holds today. Treat the "
                f"realised figure as the one about the book you hold now.")
    return out
