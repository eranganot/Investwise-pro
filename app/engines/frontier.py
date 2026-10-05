"""PR 88 - THE EFFICIENT FRONTIER, as pure math.

Until this module the plan's targets were constants: `OBJ_TARGET` and each
catalog strategy's hand-written `target_allocation`. `portfolio_risk.analyze`
measured a full covariance matrix and nothing that decides a weight ever read
it. This is the engine the frontier pipeline is built on (PR 88-92):

    inputs      ILS returns, shrunk covariance, Black-Litterman mu     (PR 89)
    propose     THIS: the core optimised around the sleeves            (PR 88)
    stabilise   resampled weights, a no-trade band                     (PR 90)
    validate    whole-book mean-CVaR on real history                   (PR 91)
    apply       book_target, behind Accept                             (PR 92)

Pure numpy, no I/O, no scipy. Nothing here reads a plan or writes a weight.

**THE OBJECTIVE, stated once, because whatever goes in the box is what gets
optimised for:**

    maximise   mu'x - (lambda/2) x'Sx
    over       x = fixed + free          (fixed: the sleeves, never moved)
    subject to 0 <= free_i <= upper_i,   sum(free) = free_total

The FIXED part is the point. The book's sleeves are chosen one at a time and are
not this optimiser's to resize; they still sit in the covariance, so the free
weights are chosen for the book they will live in. A core picked on its own is
picked for a book that does not exist -- next to 64% of leveraged Nasdaq, the
best core is not the best standalone 60/40.

**THE RETURN CONVENTION.** Every stated return in this app is a CAGR
(`return_convention`). Mean-variance and Kelly both want the ARITHMETIC
(continuous) drift: `drift_pct` converts, once, and `growth` converts back with
expm1, so a figure going in and a figure coming out are read the same way.

**KELLY AND LAMBDA ARE THE SAME DIAL.** Unconstrained, the objective's optimum
is (1/lambda) S^-1 (mu - r): lambda = 1 is full Kelly, the weights that maximise
long-run log growth, and lambda = 2 is half Kelly. So the risk a plan takes is
stated as a FRACTION OF KELLY, never as an unexplained lambda. Full Kelly is
never offered: the inputs are estimates, and betting the full growth-optimal
amount on an estimated mu over-bets by construction.

With the budget constraint (the free weights must sum to `free_total`) the
mapping is exact only along the direction it is not binding; a constant added
to every mu does not change the argmax, so passing total or excess returns
gives the same weights. Cash is an asset like any other here (BIL), not a
residual.
"""
from __future__ import annotations

import math

import numpy as np

from app.engines.return_convention import drift_pct

FRONTIER_VERSION = "f1"
TRADING_DAYS = 252

# The risk a plan takes, as a fraction of full Kelly. Decided with Eran
# 2026-10-05. Never 1.0: full Kelly on estimated inputs over-bets.
KELLY_FRACTION_BY_OBJECTIVE = {
    "Grow": 0.5,
    "Balanced": 1.0 / 3.0,
    "Preserve": 0.25,
    "Income": 0.25,
}
DEFAULT_OBJECTIVE = "Balanced"

# The solver. Projected gradient on a concave quadratic converges; these bound
# the work, they are not tuning knobs that change the answer.
MAX_ITER = 20_000
TOL = 1e-10


# --------------------------------------------------------------------------
# conventions
# --------------------------------------------------------------------------

def arithmetic_drift(cagr_pct: float, volatility_pct: float) -> float:
    """CAGR (%) -> arithmetic drift as a FRACTION, via `return_convention`."""
    return drift_pct(cagr_pct, volatility_pct) / 100.0


# PR 91: the fraction is the user's to set (Eran, 2026-10-05: "0.5 is the
# default but I want to be able to change it"). Capped at full Kelly because
# past it the model gives LESS compound growth for MORE risk -- a setting that
# can only make both numbers worse is not a choice worth offering.
KELLY_MIN = 0.25
KELLY_MAX = 1.0


def kelly_fraction_for(objective: str | None, override: float | None = None) -> float:
    """The plan's fraction of full Kelly: its own setting, else the objective's."""
    if override is not None:
        return min(KELLY_MAX, max(KELLY_MIN, float(override)))
    return KELLY_FRACTION_BY_OBJECTIVE.get(objective or DEFAULT_OBJECTIVE,
                                           KELLY_FRACTION_BY_OBJECTIVE[DEFAULT_OBJECTIVE])


def risk_aversion_for(objective: str | None, fraction: float | None = None) -> float:
    """lambda for a plan: 1 / (its fraction of Kelly)."""
    return 1.0 / kelly_fraction_for(objective, fraction)


# --------------------------------------------------------------------------
# covariance
# --------------------------------------------------------------------------

def ledoit_wolf(returns: np.ndarray, *, periods: int = TRADING_DAYS) -> tuple[np.ndarray, float]:
    """Shrunk covariance of a T x N return matrix, annualised by `periods`.

    Ledoit & Wolf (2004), shrinking toward a scaled identity. Returns
    `(covariance, shrinkage)` with shrinkage in [0, 1] -- reported rather than
    hidden, because a shrinkage near 1 says the sample said almost nothing.

    Why not `np.cov`: the sample covariance is the input mean-variance is most
    sensitive to after mu, and with a few hundred sessions over a handful of
    leveraged, highly correlated instruments it is close to singular. An
    optimiser fed a near-singular matrix finds "hedges" that are estimation
    noise and bets on them hardest.
    """
    x = np.asarray(returns, dtype=float)
    if x.ndim != 2 or x.shape[0] < 2 or x.shape[1] < 1:
        raise ValueError("ledoit_wolf needs a T x N matrix with T >= 2")
    t, n = x.shape
    x = x - x.mean(axis=0)
    sample = x.T @ x / t
    mu = float(np.trace(sample)) / n
    target = mu * np.eye(n)
    d2 = float(np.sum((sample - target) ** 2))
    if d2 <= 0.0:
        return sample * periods, 0.0
    # b2: the variance of the sample covariance's entries, bounded by d2.
    # sum_t ||x_t x_t' - S||^2 = sum_t ||x_t||^4 - T ||S||^2, because
    # sum_t x_t' S x_t = T tr(S^2). Vectorised (PR 90): the per-row loop it
    # replaces was the cost of every resampling draw.
    b2 = (float(np.sum(np.sum(x * x, axis=1) ** 2)) - t * float(np.sum(sample ** 2))) / t ** 2
    b2 = min(b2, d2)
    shrink = b2 / d2
    cov = shrink * target + (1.0 - shrink) * sample
    return cov * periods, float(shrink)


# --------------------------------------------------------------------------
# the feasible set
# --------------------------------------------------------------------------

def project_capped_simplex(v: np.ndarray, upper: np.ndarray, total: float) -> np.ndarray:
    """Euclidean projection onto {0 <= x <= upper, sum(x) = total}.

    The projection is clip(v - tau, 0, upper) for the one tau that makes it sum
    to `total`. That sum is piecewise LINEAR and decreasing in tau, with kinks
    only at v_i and v_i - upper_i, so tau is solved exactly on the one segment
    that brackets `total`. (It was a bisection until PR 90; with caps binding it
    rarely met its stopping rule, and it was the whole cost of a resample.)
    """
    v = np.asarray(v, dtype=float)
    upper = np.asarray(upper, dtype=float)
    if total < -1e-12 or total > float(upper.sum()) + 1e-9:
        raise ValueError(f"infeasible: total {total} outside [0, {float(upper.sum())}]")
    bps = np.unique(np.concatenate([v, v - upper]))

    def f(tau: float) -> float:
        return float(np.clip(v - tau, 0.0, upper).sum())

    # f(min bp) = sum(upper) >= total >= 0 = f(max bp): one segment brackets it.
    vals = np.array([f(b) for b in bps])
    j = int(np.searchsorted(-vals, -total, side="left"))
    if j == 0:
        tau = float(bps[0])
    else:
        a, b, fa, fb = bps[j - 1], bps[j], vals[j - 1], vals[j]
        tau = float(a if fa == fb else a + (fa - total) * (b - a) / (fa - fb))
    return np.clip(v - tau, 0.0, upper)


def _prepare(mu, cov, fixed, free, upper, free_total):
    mu = np.asarray(mu, dtype=float)
    cov = np.asarray(cov, dtype=float)
    n = mu.size
    if cov.shape != (n, n):
        raise ValueError(f"cov is {cov.shape}, mu has {n} entries")
    fixed = np.zeros(n) if fixed is None else np.asarray(fixed, dtype=float)
    free = np.ones(n, dtype=bool) if free is None else np.asarray(free, dtype=bool)
    if fixed.shape != (n,) or free.shape != (n,):
        raise ValueError("fixed and free must have one entry per asset")
    if np.any(fixed[free] != 0.0):
        raise ValueError("a free asset cannot also carry a fixed weight")
    if not free.any():
        raise ValueError("nothing is free to optimise")
    up = np.full(n, float(free_total)) if upper is None else np.asarray(upper, dtype=float)
    up = np.minimum(up, float(free_total))
    return mu, cov, fixed, free, up[free]


def optimize(mu, cov, *, risk_aversion: float, free_total: float,
             fixed=None, free=None, upper=None) -> np.ndarray:
    """The full weight vector maximising mu'x - (lambda/2) x'Sx.

    `fixed` is a length-N vector of weights that do not move (zero on free
    assets); `free` a boolean mask of the assets being chosen; `upper` per-asset
    caps (only the free entries are read). The free weights sum to
    `free_total`, so `free_total + sum(fixed)` is what the book holds in total.

    `risk_aversion = inf` is minimum variance: the return estimate is ignored,
    which is the honest fallback when no return has been measured.
    """
    mu, cov, fixed, free, up = _prepare(mu, cov, fixed, free, upper, free_total)
    lam = float(risk_aversion)
    if lam <= 0:
        raise ValueError("risk_aversion must be positive")
    min_var = math.isinf(lam)
    # Objective in the free weights y: f(y) = a'y - 1/2 y'Qy
    s_ff = cov[np.ix_(free, free)]
    cross = cov[np.ix_(free, ~free)] @ fixed[~free] if (~free).any() else np.zeros(free.sum())
    if min_var:
        a, q = -cross, s_ff
    else:
        a, q = mu[free] - lam * cross, lam * s_ff
    step = 1.0 / max(float(np.linalg.eigvalsh(q).max()), 1e-12)
    m = int(free.sum())
    y = project_capped_simplex(np.full(m, free_total / m), up, free_total)
    # FISTA (accelerated projected gradient), PR 90. Plain projected gradient
    # converges at the rate of the condition number, and a book holding an
    # 88%-volatility sleeve beside a 10% bond fund is badly conditioned: on the
    # live book it ran into MAX_ITER and a 100-draw resample took 62s. Same
    # fixed point, same feasible set; only the path to it is shorter.
    z, t = y.copy(), 1.0
    for _ in range(MAX_ITER):
        y_new = project_capped_simplex(z + step * (a - q @ z), up, free_total)
        if float(np.max(np.abs(y_new - y))) < TOL:
            y = y_new
            break
        t_new = 0.5 * (1.0 + math.sqrt(1.0 + 4.0 * t * t))
        z = y_new + ((t - 1.0) / t_new) * (y_new - y)
        y, t = y_new, t_new
    out = fixed.copy()
    out[free] = y
    return out


def portfolio_stats(weights, mu, cov) -> dict:
    """Arithmetic return and volatility of a weight vector, as fractions."""
    w = np.asarray(weights, dtype=float)
    ret = float(w @ np.asarray(mu, dtype=float))
    vol = math.sqrt(max(0.0, float(w @ np.asarray(cov, dtype=float) @ w)))
    return {"return": ret, "volatility": vol}


# --------------------------------------------------------------------------
# the frontier
# --------------------------------------------------------------------------

def frontier(mu, cov, *, free_total: float, fixed=None, free=None, upper=None,
             points: int = 25, lam_min: float = 0.25, lam_max: float = 200.0) -> list[dict]:
    """The constrained frontier, minimum variance first, most return last.

    Swept over lambda on a geometric grid, plus the minimum-variance end. Each
    point carries its own lambda, so a reader can say which point is half
    Kelly. Points that repeat a weight vector (the cap binds and lambda stops
    mattering) are dropped: a frontier drawn with twelve copies of one point
    reads as twelve choices.
    """
    lams = [math.inf] + list(np.geomspace(lam_max, lam_min, max(2, points - 1)))
    out: list[dict] = []
    for lam in lams:
        w = optimize(mu, cov, risk_aversion=lam, free_total=free_total,
                     fixed=fixed, free=free, upper=upper)
        if out and float(np.max(np.abs(w - out[-1]["weights"]))) < 1e-6:
            continue
        out.append({"risk_aversion": lam, "weights": w, **portfolio_stats(w, mu, cov)})
    return out


def for_volatility(mu, cov, target_volatility: float, *, free_total: float,
                   fixed=None, free=None, upper=None) -> dict:
    """The frontier point at a stated volatility (a fraction, e.g. 0.10).

    Volatility falls as lambda rises, so bisection on log(lambda). A target
    under the minimum-variance point, or over the most-return point, is not
    clipped silently: `reachable` is False and the payload says which end the
    answer sits on.
    """
    kw = dict(free_total=free_total, fixed=fixed, free=free, upper=upper)
    floor_w = optimize(mu, cov, risk_aversion=math.inf, **kw)
    floor = portfolio_stats(floor_w, mu, cov)
    if target_volatility <= floor["volatility"] + 1e-12:
        return {"weights": floor_w, **floor, "risk_aversion": math.inf,
                "reachable": target_volatility >= floor["volatility"] - 1e-9,
                "bound": "MIN_VARIANCE"}
    lo_lam = 1e-4
    top_w = optimize(mu, cov, risk_aversion=lo_lam, **kw)
    top = portfolio_stats(top_w, mu, cov)
    if target_volatility >= top["volatility"] - 1e-12:
        return {"weights": top_w, **top, "risk_aversion": lo_lam,
                "reachable": target_volatility <= top["volatility"] + 1e-9,
                "bound": "MAX_RETURN"}
    lo, hi = math.log(lo_lam), math.log(1e6)
    w, st, lam = top_w, top, lo_lam
    for _ in range(80):
        lam = math.exp(0.5 * (lo + hi))
        w = optimize(mu, cov, risk_aversion=lam, **kw)
        st = portfolio_stats(w, mu, cov)
        if abs(st["volatility"] - target_volatility) < 1e-6:
            break
        if st["volatility"] > target_volatility:
            lo = math.log(lam)
        else:
            hi = math.log(lam)
    return {"weights": w, **st, "risk_aversion": lam, "reachable": True, "bound": None}


# --------------------------------------------------------------------------
# Kelly
# --------------------------------------------------------------------------

def kelly_weights(mu, cov, risk_free: float) -> np.ndarray:
    """Unconstrained growth-optimal weights, S^-1 (mu - r). May lever or short.

    Reported as a reference, never applied: it is where the frontier's
    lambda = 1 point would sit with no budget, no caps and no long-only rule.
    """
    mu = np.asarray(mu, dtype=float)
    cov = np.asarray(cov, dtype=float)
    try:
        return np.linalg.solve(cov, mu - risk_free)
    except np.linalg.LinAlgError:
        return np.linalg.pinv(cov) @ (mu - risk_free)


def growth(weights, mu, cov, risk_free: float) -> dict:
    """Expected long-run growth of a book, continuous and as a CAGR.

    g = r + w'(mu - r) - 1/2 w'Sw, with 1 - sum(w) in cash at r. Past full
    Kelly the last term wins: more exposure LOWERS growth, which is the fact a
    leveraged book most needs to see.
    """
    w = np.asarray(weights, dtype=float)
    mu = np.asarray(mu, dtype=float)
    g = risk_free + float(w @ (mu - risk_free)) - 0.5 * float(w @ np.asarray(cov) @ w)
    return {"log_growth": g, "cagr": math.expm1(g)}


def kelly_position(weights, mu, cov, risk_free: float) -> dict:
    """How far along its OWN direction a book sits, in multiples of full Kelly.

    Scaling the book's mix by k gives growth r + k*e - 1/2 k^2 v, with
    e = w'(mu - r) and v = w'Sw; it peaks at k* = e / v. The book holds k = 1,
    so `fraction_of_kelly` = 1 / k* = v / e. Above 1.0 the book is OVER-Kelly:
    holding less of the same mix would grow faster, as well as more safely.

    A mix whose excess return is not positive has no growth-optimal size above
    zero; `fraction_of_kelly` is None and `over_kelly` True, with the reason.
    """
    w = np.asarray(weights, dtype=float)
    e = float(w @ (np.asarray(mu, dtype=float) - risk_free))
    v = float(w @ np.asarray(cov, dtype=float) @ w)
    now = growth(w, mu, cov, risk_free)
    if v <= 0.0:
        return {"fraction_of_kelly": 0.0, "over_kelly": False, "growth_now": now,
                "growth_at_kelly": now, "reason": "NO_RISK"}
    if e <= 0.0:
        return {"fraction_of_kelly": None, "over_kelly": True, "growth_now": now,
                "growth_at_kelly": growth(np.zeros_like(w), mu, cov, risk_free),
                "reason": "NO_EXCESS_RETURN"}
    k_star = e / v
    return {"fraction_of_kelly": 1.0 / k_star, "over_kelly": k_star < 1.0,
            "growth_now": now, "growth_at_kelly": growth(w * k_star, mu, cov, risk_free),
            "reason": None}
