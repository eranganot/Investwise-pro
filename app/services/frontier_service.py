"""PR 90 - the frontier acts: resize the sleeves toward the plan's Kelly fraction.

Eran, 2026-10-05, after PR 89's smoke showed a 40% sleeve at 88% volatility
with negative expected compound growth: *"I want the recommendation cards to be
actionable and not only with insights ... once I approve them will execute it."*

**WHAT THE LIVE BOOK SAID** (frontier_inputs on production, 2026-10-05). The
book as targeted sits at **1.16x full Kelly**: past the point where holding
more of the same mix adds growth. Re-optimising only the core beside fixed
sleeves gets it to 1.08x and an all-bond corner -- the sleeves carry the risk,
so a core-only card would be a card that cannot do what it is for. Letting the
sleeves shrink reaches the Grow plan's 1/2 Kelly at half the volatility.

So the actionable card is a RESIZE: shrink each sleeve the optimiser says is
oversized, sell the excess with CGT, buy the core's own basket with the
proceeds, and re-level the sleeve caps -- one Accept.

**THE PROBLEM IT IS SOLVING, stated once:**

    maximise   mu'x - (lambda/2) x'Sx         lambda = 1 / (objective's Kelly fraction)
    over       x = sleeves + core + cash
    where      each sleeve may only SHRINK (0 <= s_i <= today's size)
               the core is held to its basket's own shape (one composite asset)
               cash stays where it is

Shrink-only on purpose: growing a leveraged sleeve because an estimate says so
is the trade this pipeline least trusts itself to make.

**WHY IT DOES NOT ACT ON ONE ESTIMATE.** Mean-variance amplifies whatever error
is in its inputs, and these inputs rest on one decade and one bear market.
The solve is repeated on `DRAWS` block-bootstrapped histories (21-day blocks
keep volatility clustering intact); the proposal is the AVERAGE of those
answers (Michaud resampling) and the 10th-90th percentile is the stability
band. A sleeve is only resized when today's size sits ABOVE its band -- when
the sampling noise cannot explain the gap. Inside the band the card does not
exist: a recommendation that flips with the bootstrap seed is not one.

**WHAT THE CARD MAY CLAIM.** Every before/after figure is computed from the
SIMULATED post-trade weights in the same model (CLAUDE.md: never from trade
size), and the accept test re-measures the book the handler actually produced.

Computed nightly and on demand, stored as a `SolverVerdict` row (version tag
`FRONTIER_TAG`), and read by the card -- never computed on the Today path.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import numpy as np

from app.engines import frontier as fr
from app.engines import paths
from app.services import frontier_inputs as fi
from app.services.solver_store import FRONTIER_VERSION

logger = logging.getLogger(__name__)

FRONTIER_TAG = FRONTIER_VERSION     # SolverVerdict.solver_version, owned by solver_store
DRAWS = 100
BLOCK_DAYS = 21
SEED = 7
BAND_PCT = (10.0, 90.0)
# A resize smaller than this (percentage points of NAV) is not a card: it is
# inside lot granularity and the estimate's own noise.
MIN_MOVE_PCT = 2.0                # default; setting `min_resize_pts` (PR 115)


def _min_move() -> float:
    from app.services import thresholds
    return float(thresholds.get("min_resize_pts", MIN_MOVE_PCT))


def _material_gap() -> float:
    from app.services import thresholds
    return float(thresholds.get("material_gap_close_pct", MATERIAL_GAP_CLOSE * 100.0)) / 100.0


def _material_vol() -> float:
    from app.services import thresholds
    return float(thresholds.get("material_vol_drop_pts", MATERIAL_VOL_DROP_PCT))
# Proposals land on a half-point grid. Finer would print precision the
# resampling does not have.
GRID_PCT = 0.5
# Past this a verdict is not acted on: the nightly job has missed a run.
MAX_AGE_H = 36.0

RESIZE = "RESIZE"
WITHIN_BAND = "WITHIN_BAND"
NOT_MEASURABLE = "NOT_MEASURABLE"
UNAVAILABLE = "UNAVAILABLE"          # the price feed refused, twice; says nothing about the book
IMMATERIAL = "IMMATERIAL"            # stable, but too small to be worth the tax and the trades

# A resize must EARN its card. Measured on the live book under 'blended': the
# stable answer was "sell the whole QQQ sleeve" to move 0.64x -> 0.61x Kelly
# and 41.9% -> 39.9% volatility -- real, resampling-proof, and not worth a
# taxable exit. It becomes a card only when it closes at least a third of the
# gap to the plan's Kelly fraction OR cuts volatility by 3 points.
MATERIAL_GAP_CLOSE = 1.0 / 3.0
MATERIAL_VOL_DROP_PCT = 3.0

# Past the provider breaker's recovery (`provider_cb_recovery_sec`, 30s).
RETRY_AFTER_S = 35.0

CARD_KIND = "frontier_resize"


# --------------------------------------------------------------------------
# pure
# --------------------------------------------------------------------------

def proxy_of(ticker: str) -> str:
    """Which frontier asset a core basket ticker is modelled by."""
    from app.services.allocation_mix import classify
    from app.services.strategy_profile import _INTL
    tk = (ticker or "").upper()
    if tk == "CASH":
        return fi.CASH_ID
    cls = classify(tk, "")
    if cls == "Fixed Income":
        return "fixed_income"
    if cls == "Commodities":
        return "commodities"
    if cls == "Cash":
        return fi.CASH_ID
    return "intl_equity" if tk in _INTL else "us_equity"


def core_vector(basket: dict[str, float], ids: list[str]) -> np.ndarray | None:
    """The core basket as weights over the frontier's asset ids (sums to 1).

    None when a basket ticker maps to an asset that could not be priced: a
    composite missing a component would be a different core than the one
    being bought.
    """
    total = sum(w for w in basket.values() if w > 0)
    if total <= 0:
        return None
    v = np.zeros(len(ids))
    for tk, w in basket.items():
        if w <= 0:
            continue
        pid = proxy_of(tk)
        if pid not in ids:
            return None
        v[ids.index(pid)] += w / total
    return v


def block_bootstrap(t: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """Row indices for one stationary-block redraw of a length-t history."""
    starts = rng.integers(0, max(1, t - block + 1), size=math.ceil(t / block))
    idx = np.concatenate([np.arange(s, min(s + block, t)) for s in starts])
    return idx[:t]


def _expand(w_r: np.ndarray, sleeve_idx: list[int], core_vec: np.ndarray, cash_idx: int,
            n: int) -> np.ndarray:
    """[sleeves..., core, cash] -> weights over every asset: `_reduce` inverted."""
    k = len(sleeve_idx)
    w = core_vec * float(w_r[k])
    for j, i in enumerate(sleeve_idx):
        w[i] += float(w_r[j])
    w[cash_idx] += float(w_r[k + 1])
    return w[:n] if w.size >= n else np.pad(w, (0, n - w.size))


def _reduce(mu: np.ndarray, cov: np.ndarray, sleeve_idx: list[int], core_vec: np.ndarray,
            cash_idx: int) -> tuple[np.ndarray, np.ndarray]:
    """Full asset space -> [sleeves..., core, cash]."""
    n = mu.size
    rows = []
    for i in sleeve_idx:
        r = np.zeros(n)
        r[i] = 1.0
        rows.append(r)
    rows.append(core_vec)
    c = np.zeros(n)
    c[cash_idx] = 1.0
    rows.append(c)
    a = np.array(rows)
    return a @ mu, a @ cov @ a.T


def _book_metrics(w: np.ndarray, mu: np.ndarray, cov: np.ndarray, rf: float) -> dict:
    st = fr.portfolio_stats(w, mu, cov)
    kp = fr.kelly_position(w, mu, cov, rf)
    f = kp["fraction_of_kelly"]
    return {"fraction_of_kelly": round(f, 3) if f is not None else None,
            "over_kelly": kp["over_kelly"],
            "volatility_pct": round(st["volatility"] * 100, 2),
            "return_pct": round(st["return"] * 100, 2),
            "growth_cagr_pct": round(kp["growth_now"]["cagr"] * 100, 2)}


def _grid(x_pct: float) -> float:
    return round(round(x_pct / GRID_PCT) * GRID_PCT, 2)


def resize_solve(prep: dict, *, sleeves_pct: dict[str, float], core_basket: dict[str, float],
                 cash_pct: float, objective: str, est_kw: dict,
                 kelly_fraction: float | None = None, target_roi_pct: float | None = None,
                 draws: int = DRAWS, seed: int = SEED) -> dict:
    """The resampled resize. Pure: a `frontier_inputs.prepare` result in, a verdict out.

    ``sleeves_pct`` is each sleeve's PLAN size in percent of NAV; ``cash_pct``
    today's cash share in percent; ``est_kw`` the keyword arguments for
    `frontier_inputs.estimate` (its ``confidence`` is the plan's).
    ``kelly_fraction`` is the plan's own setting (None: the objective's);
    ``target_roi_pct`` the plan's ROI target, which the verdict measures
    itself against under every confidence -- the resize optimises risk, and
    the user must see what it does to the number they actually set.
    """
    assets = prep["assets"]
    ids = [a["id"] for a in assets] + [fi.CASH_ID]
    sleeve_ids = [a["id"] for a in assets if a["kind"] == "sleeve" and a["id"] in sleeves_pct]
    if not sleeve_ids:
        return {"ok": True, "outcome": NOT_MEASURABLE, "reason": "NO_SLEEVES",
                "detail": "no sleeve on this book could be measured, so there is nothing to resize"}
    core_vec = core_vector(core_basket, ids)
    if core_vec is None:
        return {"ok": True, "outcome": NOT_MEASURABLE, "reason": "CORE_NOT_MODELLED",
                "detail": "the core basket names an asset that could not be priced"}
    sleeve_idx = [ids.index(s) for s in sleeve_ids]
    cash_idx = ids.index(fi.CASH_ID)
    k = len(sleeve_ids)
    now_s = np.array([sleeves_pct[s] / 100.0 for s in sleeve_ids])
    cash = max(0.0, min(1.0, cash_pct / 100.0))
    free_total = 1.0 - cash
    if now_s.sum() > free_total + 1e-9:
        return {"ok": True, "outcome": NOT_MEASURABLE, "reason": "SLEEVES_EXCEED_BOOK",
                "detail": "sleeves plus cash exceed the book"}
    frac = fr.kelly_fraction_for(objective, kelly_fraction)
    lam = 1.0 / frac
    free = np.array([True] * (k + 1) + [False])
    fixed = np.zeros(k + 2)
    fixed[-1] = cash
    upper = np.append(now_s, [1.0, 0.0])

    def solve(mu_r, cov_r):
        return fr.optimize(mu_r, cov_r, risk_aversion=lam, free_total=free_total,
                           fixed=fixed, free=free, upper=upper)

    ils = prep["ils"]
    est0 = fi.estimate(ils, assets, **est_kw)
    mu0, cov0 = _reduce(est0["mu"], est0["cov"], sleeve_idx, core_vec, cash_idx)
    point = solve(mu0, cov0)

    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(draws):
        idx = block_bootstrap(ils.shape[0], BLOCK_DAYS, rng)
        est = fi.estimate(ils[idx], assets, **est_kw)
        mu_r, cov_r = _reduce(est["mu"], est["cov"], sleeve_idx, core_vec, cash_idx)
        samples.append(solve(mu_r, cov_r)[:k])
    samples = np.array(samples)
    mean = samples.mean(axis=0)
    lo, hi = np.percentile(samples, BAND_PCT, axis=0)

    rows, proposed = [], now_s.copy()
    for j, sid in enumerate(sleeve_ids):
        cur = now_s[j] * 100
        prop = _grid(mean[j] * 100)
        act = (cur - hi[j] * 100) >= _min_move() and (cur - prop) >= _min_move()
        if act:
            proposed[j] = prop / 100.0
        rows.append({"id": sid, "current_pct": round(cur, 2), "proposed_pct": prop,
                     "point_pct": round(point[j] * 100, 2),
                     "band_pct": [round(lo[j] * 100, 2), round(hi[j] * 100, 2)],
                     "act": bool(act)})

    def book(s):
        return np.concatenate([s, [free_total - s.sum(), cash]])

    rf = est0["risk_free"]
    before = _book_metrics(book(now_s), mu0, cov0, rf)
    after = _book_metrics(book(proposed), mu0, cov0, rf)
    outcome = RESIZE if any(r["act"] for r in rows) else WITHIN_BAND
    materiality = None
    if outcome == RESIZE:
        gap_b = abs((before["fraction_of_kelly"] or 0.0) - frac)
        gap_a = abs((after["fraction_of_kelly"] or 0.0) - frac)
        closed = (gap_b - gap_a) / gap_b if gap_b > 0 else 0.0
        vol_drop = before["volatility_pct"] - after["volatility_pct"]
        material = closed >= _material_gap() or vol_drop >= _material_vol()
        materiality = {"gap_closed": round(closed, 3), "vol_drop_pct": round(vol_drop, 2),
                       "material": material}
        if not material:
            outcome = IMMATERIAL

    # ---- the user's target, under every confidence -----------------------
    # The most these assets can compound is the full-Kelly optimum over the
    # long-only book (sleeves free to GROW here: it is a ceiling, not a
    # proposal). For log growth that objective is exact: g = r + w'(mu - r)
    # - w'Sw/2 is the lambda = 1 objective up to a constant.
    big = np.append(np.full(k, free_total), [free_total, 0.0])
    scenarios = {}
    for conf in fi.CONFIDENCE_LEVELS:
        est_c = est0 if conf == est0["confidence"] else fi.estimate(ils, assets, **{**est_kw, "confidence": conf})
        mu_c, cov_c = _reduce(est_c["mu"], est_c["cov"], sleeve_idx, core_vec, cash_idx)
        w_max = fr.optimize(mu_c, cov_c, risk_aversion=1.0, free_total=free_total,
                            fixed=fixed, free=free, upper=big)
        scenarios[conf] = {
            "before_growth_pct": round(fr.growth(book(now_s), mu_c, cov_c, rf)["cagr"] * 100, 2),
            "after_growth_pct": round(fr.growth(book(proposed), mu_c, cov_c, rf)["cagr"] * 100, 2),
            "max_growth_pct": round(fr.growth(w_max, mu_c, cov_c, rf)["cagr"] * 100, 2)}
    target = None
    if target_roi_pct is not None:
        t = float(target_roi_pct)
        mine = scenarios[est0["confidence"]]
        target = {"roi_pct": t, "confidence": est0["confidence"],
                  "reachable": mine["max_growth_pct"] >= t,
                  "before_meets": mine["before_growth_pct"] >= t,
                  "after_meets": mine["after_growth_pct"] >= t,
                  # The case the card must say out loud: the resize looks right
                  # on the chosen estimate but would take the book below the
                  # target if the backtests are right.
                  "measured_drops_below": (scenarios[fi.CONFIDENCE_MEASURED]["before_growth_pct"] >= t
                                           > scenarios[fi.CONFIDENCE_MEASURED]["after_growth_pct"])}
    return {"ok": True, "outcome": outcome, "objective": objective,
            "kelly_target": round(frac, 4), "kelly_fraction": round(frac, 4),
            "kelly_source": "plan" if kelly_fraction is not None else "objective",
            "confidence": est0["confidence"], "scenarios": scenarios, "target": target,
            "materiality": materiality,
            "risk_aversion": lam,
            "sleeves": rows, "cash_pct": round(cash * 100, 2),
            "core_pct_before": round((free_total - now_s.sum()) * 100, 2),
            "core_pct_after": round((free_total - proposed.sum()) * 100, 2),
            "core_basket": core_basket,
            "before": before, "after": after,
            "draws": draws, "block_days": BLOCK_DAYS, "band": list(BAND_PCT),
            "views": est0["views"], "shrinkage": round(est0["shrinkage"], 4),
            "window": {"start": prep["dates"][0], "end": prep["dates"][-1],
                       "sessions": len(prep["dates"])},
            "fx_basis": "historical" if prep["fx"] else "spot",
            "excluded": prep["excluded"], "degraded": prep["degraded"] + est0["degraded"]}


# --------------------------------------------------------------------------
# PR 92: the goal decides; the frontier and Kelly are how
# --------------------------------------------------------------------------
#
# Eran, 2026-10-05: "the end goal is to meet the ROI goals the user set ... the
# kelly parameter and the efficient frontier are tools and not the
# objectives." His two decisions the same day:
#   * meeting the goal = at least a 70% chance of reaching it  (GOAL_CONFIDENCE)
#   * the goal picks the risk, hard-capped at FULL Kelly; his Kelly dial is an
#     optional extra limit, never the aim                    (goal_ceiling)
#
# Measured on the live book before writing this: the 1/2-Kelly resize PR 91
# proposes LOWERS the goal's odds under every estimate (shrunk 23% -> 5%,
# blended 46% -> 41%, measured 71% -> 71%), and "the lowest risk whose MEDIAN
# reaches the goal" trims a 71% book to a coin flip. Both are why the rule
# below is about a probability, chosen by the user, inside a growth-safe cap.

GOAL_CONFIDENCE = 0.70
# The crash-risk limit's DEFAULT (Eran, 2026-10-05: "Guard 15%: 0.8% / 22.7%"). Since PR 104c
# the plan's `halve_limit_pct` decides; this is what an unset plan gets.
HALVE_FLOOR_PCT = 1.0          # Eran, 2026-10-05: "Guard 15%: 0.8% / 22.7%" -- a 1% crash-risk limit
GOAL_ON_TRACK = "ON_TRACK"            # >= 70% today, and no materially safer mix keeps it there
GOAL_LESS_RISK = "LESS_RISK"          # >= 70% today; a materially less volatile mix keeps >= 70%
GOAL_REBALANCE = "REBALANCE"          # < 70% today; a mix within the cap reaches 70%
GOAL_IMPROVE = "IMPROVE"              # < 70% everywhere within the cap; a mix raises the odds materially
GOAL_OUT_OF_REACH = "OUT_OF_REACH"    # < 70% everywhere; no trade helps materially -- the levers do
GOAL_NO_TARGET = "NO_TARGET"          # no ROI target / deadline: there is no goal to steer by
# PR 93: verdicts the historical paths overrule.
GOAL_TAIL_DISAGREES = "TAIL_DISAGREES"  # the model's trade lowers the odds on fat-tailed history
GOAL_TAIL_SHORT = "TAIL_SHORT"          # >= 70% on the model, < 70% on history: not on track
# Odds within this many points on 2,000 paths are sampling noise, not a verdict.
TAIL_TOLERANCE_PTS = 1.0

# A goal card must change the goal's odds or its risk: five points of
# probability, or three of volatility with the odds still >= GOAL_CONFIDENCE.
MATERIAL_PROB_PTS = 5.0


def _material() -> float:
    """PR 104d: the smallest goal-odds gain worth a card is a setting; 5 points by default."""
    from app.services import thresholds
    return float(thresholds.get("material_odds_pts", MATERIAL_PROB_PTS))
GRID_POINTS = 16
LAMBDA_MAX = 200.0
# Fewer draws than the risk-mode resize: each goal draw walks a 16-point
# frontier, and on the live book 100 x 24 took 77s for the shrunk estimate.
GOAL_DRAWS = 60


def goal_ceiling(kelly_setting: float | None) -> float:
    """The most of full Kelly the goal may use: the user's dial if he set one, else 1.0."""
    if kelly_setting is None:
        return fr.KELLY_MAX
    return min(fr.KELLY_MAX, max(fr.KELLY_MIN, float(kelly_setting)))


def _z(p: float) -> float:
    """Standard normal quantile (Acklam's rational approximation; |err| < 1e-9)."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549671010114055e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - 0.02425:
        return -_z(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _goal_mix(points: list, *, p_star: float):
    """The decision rule on ONE estimate's frontier -> (index, reaches).

    ``points`` run from the cap (most risk allowed) to the safest, each with
    its goal probability. Reached: the SAFEST point still >= p_star -- risk the
    goal does not need is cost with no benefit. Not reached: the point with the
    best odds (ties go to the safer one).
    """
    reach = [i for i, p in enumerate(points) if p["p"] >= p_star]
    if reach:
        return max(reach), True
    best = max(range(len(points)), key=lambda i: (round(points[i]["p"], 6), i))
    return best, False


def goal_solve(prep: dict, *, sleeves_pct: dict[str, float], core_basket: dict[str, float],
               cash_pct: float, objective: str, est_kw: dict, nav: float,
               target: float | None, years: float | None, monthly: float = 0.0,
               kelly_setting: float | None = None, sleeve_cap_pct: float = 40.0,
               volatility_cap_pct: float | None = None, p_star: float = GOAL_CONFIDENCE,
               draws: int = GOAL_DRAWS, seed: int = SEED, rules: dict | None = None,
               goal_roi: float | None = None, light: bool = False,
               halve_floor_pct: float = HALVE_FLOOR_PCT) -> dict:
    """Which mix best serves the user's goal. Pure.

    ``goal_roi`` (PR 99) is the plan's yearly ROI when the goal is "this ROI on
    every shekel put in": a deposit or a later deadline then RAISES the goal,
    and the levers are solved against the goal as it would be after Accept.
    None for a fixed amount, which no deposit or date changes.

    The OBJECTIVE is the goal: `target` (the amount his ROI implies) by his
    deadline (`years` from now), from today's `nav` plus active standing orders
    (`monthly`), reached with probability >= `p_star`. Risk is capped at
    `goal_ceiling(kelly_setting)` of full Kelly; the frontier between that cap
    and the safest mix is the menu. Sleeves may shrink or GROW (to
    `sleeve_cap_pct`, the plan's concentration cap, or today's size if larger);
    the core is held to its basket; cash is fixed.
    """
    assets = prep["assets"]
    ids = [a["id"] for a in assets] + [fi.CASH_ID]
    sleeve_ids = [a["id"] for a in assets if a["kind"] == "sleeve" and a["id"] in sleeves_pct]
    core_vec = core_vector(core_basket, ids)
    if core_vec is None:
        return {"ok": True, "mode": "goal", "outcome": NOT_MEASURABLE, "reason": "CORE_NOT_MODELLED",
                "detail": "the core basket names an asset that could not be priced"}
    if not target or not years or years <= 0 or nav <= 0:
        return {"ok": True, "mode": "goal", "outcome": GOAL_NO_TARGET,
                "detail": "no ROI target and deadline on the plan, so there is no goal to steer by"}
    g_req = fr.required_log_growth(nav, target, years, monthly)

    sleeve_idx = [ids.index(s) for s in sleeve_ids]
    cash_idx = ids.index(fi.CASH_ID)
    k = len(sleeve_ids)
    now_s = np.array([sleeves_pct[s] / 100.0 for s in sleeve_ids])
    cash = max(0.0, min(1.0, cash_pct / 100.0))
    free_total = 1.0 - cash
    ceiling = goal_ceiling(kelly_setting)
    lams = list(np.geomspace(1.0 / ceiling, LAMBDA_MAX, GRID_POINTS))
    free = np.array([True] * (k + 1) + [False])
    fixed = np.zeros(k + 2)
    fixed[-1] = cash
    upper = np.append(np.maximum(now_s, sleeve_cap_pct / 100.0), [1.0, 0.0])

    def book(s):
        return np.concatenate([s, [free_total - s.sum(), cash]])

    def prob(w, mu_r, cov_r, rf):
        g = fr.growth(w, mu_r, cov_r, rf)["log_growth"]
        vol = fr.portfolio_stats(w, mu_r, cov_r)["volatility"]
        return fr.goal_probability(nav, target, years, g, vol, monthly)

    def decide(est):
        mu_r, cov_r = _reduce(est["mu"], est["cov"], sleeve_idx, core_vec, cash_idx)
        pts = []
        for lam in lams:
            w = fr.optimize(mu_r, cov_r, risk_aversion=lam, free_total=free_total,
                            fixed=fixed, free=free, upper=upper)
            pts.append({"w": w, "lam": lam, "p": prob(w, mu_r, cov_r, est["risk_free"])})
        i, ok = _goal_mix(pts, p_star=p_star)
        return pts[i]["w"], ok, mu_r, cov_r

    ils = prep["ils"]
    est0 = fi.estimate(ils, assets, **est_kw)
    w0, reach0, mu0, cov0 = decide(est0)
    rf = est0["risk_free"]
    rng = np.random.default_rng(seed)
    samples = [decide(fi.estimate(ils[block_bootstrap(ils.shape[0], BLOCK_DAYS, rng)], assets, **est_kw))[0][:k]
               for _ in range(draws)]
    samples = np.array(samples) if draws else np.array([w0[:k]])
    mean = samples.mean(axis=0)
    lo, hi = np.percentile(samples, BAND_PCT, axis=0)

    rows, proposed = [], now_s.copy()
    for j, sid in enumerate(sleeve_ids):
        cur = now_s[j] * 100
        prop = _grid(mean[j] * 100)
        outside = cur > hi[j] * 100 + 1e-9 or cur < lo[j] * 100 - 1e-9
        act = outside and abs(cur - prop) >= _min_move()
        if act:
            proposed[j] = prop / 100.0
        rows.append({"id": sid, "current_pct": round(cur, 2), "proposed_pct": prop,
                     "point_pct": round(w0[j] * 100, 2),
                     "band_pct": [round(lo[j] * 100, 2), round(hi[j] * 100, 2)],
                     "act": bool(act), "direction": ("grow" if prop > cur else "shrink") if act else None})
    if proposed.sum() > free_total + 1e-9:
        proposed *= free_total / proposed.sum()

    def metrics(s):
        w = book(s)
        g = fr.growth(w, mu0, cov0, rf)
        vol = fr.portfolio_stats(w, mu0, cov0)["volatility"]
        kp = fr.kelly_position(w, mu0, cov0, rf)["fraction_of_kelly"]
        return {"growth_cagr_pct": round(g["cagr"] * 100, 2), "log_growth": g["log_growth"],
                "volatility": vol, "volatility_pct": round(vol * 100, 2),
                "fraction_of_kelly": round(kp, 3) if kp is not None else None,
                "goal_probability_pct": round(fr.goal_probability(nav, target, years, g["log_growth"], vol,
                                                                  monthly) * 100, 1),
                "median_at_deadline": round(fr.median_wealth(nav, g["log_growth"], years, monthly), 0)}
    before, after = metrics(now_s), metrics(proposed)
    acting = any(r["act"] for r in rows)
    star = p_star * 100
    ok_now, ok_after = before["goal_probability_pct"] >= star, after["goal_probability_pct"] >= star
    dp = after["goal_probability_pct"] - before["goal_probability_pct"]
    dvol = before["volatility_pct"] - after["volatility_pct"]
    if ok_now:
        material = acting and ok_after and dvol >= _material_vol()
        outcome = GOAL_LESS_RISK if material else GOAL_ON_TRACK
    elif reach0:
        material = acting and ok_after and dp > 0
        outcome = GOAL_REBALANCE if material else GOAL_IMPROVE if acting and dp >= _material() else GOAL_OUT_OF_REACH
        material = material or (acting and dp >= _material())
    else:
        material = acting and dp >= _material()
        outcome = GOAL_IMPROVE if material else GOAL_OUT_OF_REACH

    # ---- PR 93: the same question on real, fat-tailed history -------------
    # History's shape, the user's drift (see engines/paths.py). A trade card
    # must not lower the goal's odds on these paths either, and "on track" has
    # to hold on both. Seeded, so the same book gives the same numbers.
    n_full = len(ids)
    rf_daily = rf / paths.TRADING_DAYS
    daily = np.column_stack([paths.recentre(ils, est0["mu"][:-1], vol_scale=est_kw.get("vol_scale")), np.full(ils.shape[0], rf_daily)])

    def tail_of(s):
        w_full = _expand(book(s), sleeve_idx, core_vec, cash_idx, n_full)
        return paths.goal_stats(paths.simulate(daily @ w_full, years=years), nav=nav, target=target,
                                monthly=monthly, p_star=p_star)
    tail_before = tail_of(now_s)
    tail_after = tail_of(proposed) if acting else tail_before
    tail_gate = None
    if material and outcome in (GOAL_LESS_RISK, GOAL_REBALANCE, GOAL_IMPROVE) \
            and tail_after["p_goal_pct"] < tail_before["p_goal_pct"] - TAIL_TOLERANCE_PTS:
        tail_gate = "TRADE_LOWERS_ODDS_ON_HISTORY"
        material, outcome = False, GOAL_TAIL_DISAGREES
    if outcome in (GOAL_ON_TRACK, GOAL_LESS_RISK) and min(
            tail_before["p_goal_pct"], tail_after["p_goal_pct"] if material else 100.0) < star:
        tail_gate = tail_gate or "SHORT_OF_TARGET_ON_HISTORY"
        outcome = GOAL_TAIL_SHORT

    # ---- PR 95: the user's protective rules, on the same history ---------
    # Three runs on IDENTICAL paths: no rule acting, his rules as the app
    # executes them today, and the stop re-entry fix (buy back only after a
    # 15% rebound). The differences are what the rules -- and the fix -- are
    # worth to the goal. Reported, not yet a decision input.
    rules_block = None
    run = sl_specs = g_spec = None
    if rules:
        a_rows = []
        for i in sleeve_idx:
            e = np.zeros(n_full)
            e[i] = 1.0
            a_rows.append(e)
        a_rows.append(core_vec)
        e = np.zeros(n_full)
        e[cash_idx] = 1.0
        a_rows.append(e)
        unit_daily = daily @ np.array(a_rows).T
        # PR 123 (D85): a held ticker with a rule is its own column, carved out of
        # the core's share, so its stop acts on its own path. Its history keeps its
        # own shape and volatility (scaled like every other column) and is
        # re-centred on the core's drift: the replay asks what the RULE does, not
        # whether this stock beats the market.
        held = [(tk, h) for tk, h in (rules.get("holding_returns") or {}).items()
                if h is not None and len(h) == unit_daily.shape[0] and (rules.get("holdings") or {}).get(tk)]
        w_book = book(now_s)
        core_room = float(w_book[k])
        held_w = []
        for tk, _h in held:
            w = min(max(0.0, float((rules.get("holding_weights") or {}).get(tk) or 0.0)), core_room)
            held_w.append(w)
            core_room -= w
        if held:
            core_col = unit_daily[:, k]
            # `vol_scale` is one factor per asset in production (fi.vol_scale), a
            # scalar or absent in tests: a held ticker takes the book-wide mean.
            _vs = est_kw.get("vol_scale")
            vs_h = float(np.mean(_vs)) if _vs is not None else 1.0
            cols = [(np.asarray(h) - np.mean(h)) * vs_h + core_col.mean() for _tk, h in held]
            unit_daily = np.column_stack([unit_daily[:, :k], *cols, unit_daily[:, k:]])
            w_book = np.concatenate([w_book[:k], held_w, [core_room], w_book[k + 1:]])
        sl_specs = []
        for j, sid in enumerate(sleeve_ids):
            sp = (rules.get("sleeves") or {}).get(sid) or {}
            sl_specs.append({"col": j, "id": sid, "trail": sp.get("trail"), "tp_ratio": sp.get("tp_ratio"),
                             "tp_fraction": sp.get("tp_fraction")})
        for i, (tk, _h) in enumerate(held):
            sp = rules["holdings"][tk]
            sl_specs.append({"col": k + i, "id": tk, "trail": sp.get("trail"), "tp_ratio": sp.get("tp_ratio"),
                             "tp_fraction": sp.get("tp_fraction")})
        g_spec = None
        if rules.get("guard") and k:
            vols = unit_daily[:, :k].std(axis=0)
            g_spec = {**rules["guard"], "col": int(np.argmax(vols))}

        def run(sl=None, gd="same", years_=None, monthly_=None, target_=None, **kw):
            sim = paths.simulate_rules(unit_daily, w_book, years=years if years_ is None else years_,
                                       sleeves=sl_specs if sl is None else sl,
                                       guard=g_spec if gd == "same" else gd,
                                       monthly=monthly if monthly_ is None else monthly_,
                                       nav=nav, rf_daily=rf_daily, **kw)
            return paths.rules_stats(sim, target=target if target_ is None else target_, p_star=p_star)
        r_off, r_on, r_fix = run(rules_on=False), run(), run(reentry="rebound")
        # PR 104b: a preview (light) asks "what if these rules", not "which rules".
        tuning = None if light else _tune_protection(run, sl_specs, g_spec, rules, sleeve_ids, unit_daily, k, r_on,
                                                     floor=halve_floor_pct)
        per_rule = None if light else _per_rule_impact(run, rules, sl_specs, g_spec, sleeve_ids)
        compare = None
        drop = {str(x) for x in rules.get("_compare_drop") or []}
        if drop:
            w_ = run(n_paths=PER_RULE_PATHS)
            wo = _replay_without(run, rules, sl_specs, g_spec, sleeve_ids, drop, PER_RULE_PATHS)
            compare = {"paths": PER_RULE_PATHS,
                       "with": {"p_goal_pct": w_["p_goal_pct"], "p_halve_pct": w_["p_halve_pct"]},
                       "without": {"p_goal_pct": wo["p_goal_pct"], "p_halve_pct": wo["p_halve_pct"]},
                       "goal_pts": round(w_["p_goal_pct"] - wo["p_goal_pct"], 1)}
        rules_block = {"off": r_off, "on": r_on, "rebound": r_fix,
                       "effect_pts": round(r_on["p_goal_pct"] - r_off["p_goal_pct"], 1),
                       "fix_effect_pts": round(r_fix["p_goal_pct"] - r_on["p_goal_pct"], 1),
                       "sleeves": {sid: (rules.get("sleeves") or {}).get(sid) for sid in sleeve_ids},
                       # The order every per-sleeve list below is in. A JSON object's
                       # key order does not survive Postgres JSONB (keys come back
                       # sorted), so the dict above cannot carry it.
                       "sleeve_ids": list(sleeve_ids),
                       "guard": rules.get("guard"),
                       "guard_target": sleeve_ids[g_spec["col"]] if g_spec else None,
                       "not_simulated": rules.get("not_simulated") or [],
                       "holdings_modelled": [tk for tk, _h in held],
                       "per_rule": per_rule,
                       "compare": compare,
                       "tuning": tuning,
                       "assumptions": ["every rule card is accepted the day it fires",
                                       "sleeve rules act on the sleeve's own return stream",
                                       "a held stock with a rule acts on its own price path, re-centred on "
                                       "the core's drift",
                                       "the book is topped back to target monthly from cash"]}

    # ---- PR 95: the stress case (shrunk estimate) on every goal verdict ---
    stress = None
    if est0["confidence"] != fi.CONFIDENCE_SHRUNK and not light:
        est_s = fi.estimate(ils, assets, **{**est_kw, "confidence": fi.CONFIDENCE_SHRUNK})
        mu_s, cov_s = _reduce(est_s["mu"], est_s["cov"], sleeve_idx, core_vec, cash_idx)

        def p_s(s):
            w = book(s)
            gs = fr.growth(w, mu_s, cov_s, rf)["log_growth"]
            vs = fr.portfolio_stats(w, mu_s, cov_s)["volatility"]
            return round(fr.goal_probability(nav, target, years, gs, vs, monthly) * 100, 1)
        stress = {"confidence": fi.CONFIDENCE_SHRUNK, "before_p_pct": p_s(now_s),
                  "after_p_pct": p_s(proposed) if acting else p_s(now_s)}

    # ---- PR 99: one headline, and what the suggestions are sized on -------
    # The headline is the chance with the user's rules as they work today, with
    # the same replay's rules-off figure beside it (identical paths, so the gap
    # is the rules and nothing else). Without a rules replay: the lower of the
    # model and history, the same test "on track" uses.
    tu = (rules_block or {}).get("tuning") or {}
    if rules_block:
        headline = {"p_goal_pct": rules_block["on"]["p_goal_pct"],
                    "without_rules_pct": rules_block["off"]["p_goal_pct"], "basis": "rules"}
    else:
        headline = {"p_goal_pct": min(before["goal_probability_pct"], tail_before["p_goal_pct"]),
                    "without_rules_pct": None, "basis": "model_and_history"}
    # Eran (D1 b): size the suggestions on the book AFTER "Retune your
    # protection" when that card exists, else on his rules as they are.
    sizing_rules = None
    if rules_block and tu.get("material") and tu.get("best"):
        best_c = tu["best"]
        sizing_rules = _setup_specs(best_c, sl_specs, sleeve_ids, g_spec, tu.get("guard_col"))
        sized_on, sized_stats = "after_retune", best_c["stats"]
    elif rules_block:
        sizing_rules = (None, "same")
        sized_on, sized_stats = "rules_as_they_are", rules_block["on"]
    else:
        sized_on, sized_stats = "no_rules", None

    # ---- levers, on the book AFTER the card (or today's, without one) ----
    base = after if material else before
    tail_base = tail_after if material else tail_before
    g_b, v_b = base["log_growth"], base["volatility"]
    p_sized = (sized_stats["p_goal_pct"] if sized_stats
               else min(base["goal_probability_pct"], tail_base["p_goal_pct"]))
    levers = {"required_cagr_pct": round(math.expm1(g_req) * 100, 2) if g_req is not None else None,
              "on_track_cagr_pct": base["growth_cagr_pct"], "sized_on": sized_on,
              "sized_p_goal_pct": p_sized}
    if p_sized < star and not light:
        w_full_base = _expand(book(proposed if material else now_s), sleeve_idx, core_vec, cash_idx, n_full)
        sim_base = paths.simulate(daily @ w_full_base, years=years)

        def rules_p(t_amt, m, y):
            """The lever's chance on the rules replay it is sized on (None without rules)."""
            if sizing_rules is None:
                return None
            return run(sl=sizing_rules[0], gd=sizing_rules[1], years_=y, monthly_=m,
                       target_=t_amt)["p_goal_pct"]

        # A deposit raises the goal too: each one has to grow at the goal's ROI.
        def goal_with_deposit(extra):
            return target + (_fv_monthly(extra, goal_roi, years) if goal_roi is not None else 0.0)

        def deposit_ok(extra):
            t_amt = goal_with_deposit(extra)
            if fr.goal_probability(nav, t_amt, years, g_b, v_b, monthly + extra) * 100 < star:
                return False
            return paths.goal_stats(sim_base, nav=nav, target=t_amt, monthly=monthly + extra,
                                    p_star=p_star)["p_goal_pct"] >= star
        dep, prev = None, 0.0
        for i in range(20):
            m_try = 10.0 * 2 ** i
            if deposit_ok(m_try):
                lo_m, hi_m = prev, m_try
                for _ in range(25):
                    mid = 0.5 * (lo_m + hi_m)
                    lo_m, hi_m = (lo_m, mid) if deposit_ok(mid) else (mid, hi_m)
                dep = int(math.ceil(hi_m / 10.0) * 10)
                if not deposit_ok(dep):
                    dep = int(math.ceil(m_try / 10.0) * 10)
                break
            prev = m_try
        if dep is not None:
            t_dep = goal_with_deposit(dep)
            pr = rules_p(t_dep, monthly + dep, years)
            levers["deposit_rules_p_goal_pct"] = pr
            if pr is not None and pr < star:
                dep = None                    # holds without the rules, not with them
            else:
                levers["deposit_goal_after"] = round(t_dep, 0)
        levers["extra_monthly_ils"] = dep

        # A later deadline raises the goal too: another year at the goal's ROI.
        ext, rules_tries = None, 0
        for i in range(1, 61):
            e = 0.25 * i
            t_amt = target * (1.0 + goal_roi) ** e if goal_roi is not None else target
            if fr.goal_probability(nav, t_amt, years + e, g_b, v_b, monthly) * 100 < star:
                continue
            hs = paths.goal_stats(paths.simulate(daily @ w_full_base, years=years + e), nav=nav,
                                  target=t_amt, monthly=monthly, p_star=p_star)
            if hs["p_goal_pct"] < star:
                continue
            if sizing_rules is not None:
                rules_tries += 1
                pr = rules_p(t_amt, monthly, years + e)
                if pr < star:
                    if rules_tries >= 4:
                        break
                    continue
                levers["deadline_rules_p_goal_pct"] = pr
            ext = e
            levers["deadline_goal_after"] = round(t_amt, 0)
            levers["deadline_history_p_goal_pct"] = hs["p_goal_pct"]
            break
        levers["deadline_extra_years"] = ext

        # The largest goal amount reached with p_star by the deadline, on every
        # basis the card can be held to: the model, history, and the rules
        # replay it is sized on. The smallest of them is what the card offers.
        z = _z(1.0 - p_star)
        amt_model = round(fr.fv_contributions(monthly, g_b, years)
                          + nav * math.exp(g_b * years + v_b * math.sqrt(years) * z), 0)
        amt_rules = (sized_stats or {}).get("amount_at_confidence")
        levers["amount_at_confidence_model"] = amt_model
        levers["amount_at_confidence_history"] = tail_base["amount_at_confidence"]
        levers["amount_at_confidence_rules"] = amt_rules
        levers["amount_at_confidence"] = min(x for x in (amt_model, tail_base["amount_at_confidence"], amt_rules)
                                             if x is not None)
        # Money and time cannot close a goal that asks more per shekel than the
        # book is expected to earn: said once instead of offering cards for it.
        if goal_roi is not None and dep is None and ext is None \
                and base["growth_cagr_pct"] < goal_roi * 100:
            levers["money_time_note"] = {"roi_pct": round(goal_roi * 100, 2),
                                         "expected_cagr_pct": base["growth_cagr_pct"]}
    for m in (before, after):
        m.pop("volatility", None)
    return {"ok": True, "mode": "goal", "outcome": outcome, "material": bool(material),
            "objective": objective, "p_star": p_star,
            "kelly_ceiling": round(ceiling, 4),
            "kelly_source": "plan" if kelly_setting is not None else "goal (cap 1.0x)",
            "kelly_fraction": round(ceiling, 4), "kelly_target": round(ceiling, 4),
            "confidence": est0["confidence"],
            "goal": {"target": round(target, 2), "years": round(years, 3), "nav": round(nav, 2),
                     "monthly": round(monthly, 2), "reachable_within_ceiling": bool(reach0),
                     "meets_now": bool(ok_now), "meets_after": bool(ok_after)},
            "volatility_cap_pct": volatility_cap_pct,
            "sleeves": rows, "cash_pct": round(cash * 100, 2),
            "core_pct_before": round((free_total - now_s.sum()) * 100, 2),
            "core_pct_after": round((free_total - proposed.sum()) * 100, 2),
            "core_basket": core_basket, "before": before, "after": after, "levers": levers,
            "rules": rules_block, "stress": stress, "headline": headline,
            "tail": {"before": tail_before, "after": tail_after, "gate": tail_gate,
                     "paths": paths.N_PATHS, "block_days": paths.BLOCK_DAYS,
                     "basis": "historical daily returns re-centred on the chosen estimate"},
            "draws": draws, "block_days": BLOCK_DAYS, "band": list(BAND_PCT),
            "views": est0["views"], "shrinkage": round(est0["shrinkage"], 4),
            "window": {"start": prep["dates"][0], "end": prep["dates"][-1],
                       "sessions": len(prep["dates"])},
            "fx_basis": "historical" if prep["fx"] else "spot",
            "excluded": prep["excluded"], "degraded": prep["degraded"] + est0["degraded"]}


# --------------------------------------------------------------------------
# PR 94: which core serves the goal
# --------------------------------------------------------------------------
#
# The goal solve holds the core to its basket. This asks the other question:
# would a different CORE -- one of the catalog's whole-book mixes -- give the
# goal better odds, with the sleeves as they are? Each candidate is priced from
# ITS OWN tickers' real history (never through a proxy: modelling NVDA as VTI
# is a lie by approximation), and the current core the same way, so the
# comparison is like for like. A candidate whose tickers were not fetched is
# listed, not guessed.

CORE_SWITCH = "SWITCH"
CORE_KEEP = "KEEP"
CORE_CARD_KIND = "frontier_core"
CORE_DRAWS = 30
CORE_MIN_WIN_SHARE = 0.5


def core_solve(prep: dict, *, sleeves_pct: dict[str, float], current_core_id: str | None,
               candidates: list[dict], cash_pct: float, est_kw: dict, nav: float,
               target: float, years: float, monthly: float = 0.0, objective: str = "Balanced",
               kelly_setting: float | None = None, p_star: float = GOAL_CONFIDENCE,
               draws: int = CORE_DRAWS, seed: int = SEED) -> dict:
    """Score every modellable catalog core by the goal's odds. Pure.

    Score = the LOWER of the model's and history's probability: a core has to
    help on both. Eligible only if it does not push the book further past the
    Kelly cap than it already is. A switch must beat today's core by
    `MATERIAL_PROB_PTS` on the score, not lose on either method, and win at
    least `CORE_MIN_WIN_SHARE` of the bootstrap draws.
    """
    assets = prep["assets"]
    ils = prep["ils"]
    sleeve_ids = [a["id"] for a in assets if a["kind"] == "sleeve" and a["id"] in sleeves_pct]
    cash = max(0.0, min(1.0, cash_pct / 100.0))
    s_tot = sum(sleeves_pct[s] / 100.0 for s in sleeve_ids)
    core_share = max(0.0, 1.0 - cash - s_tot)
    ceiling = goal_ceiling(kelly_setting)

    modelled, not_modelled = [], []
    for c in candidates:
        col = fi.basket_ils(prep, c["weights"])
        (modelled if col is not None else not_modelled).append((c, col))
    if current_core_id not in {c["id"] for c, _ in modelled}:
        return {"ok": True, "outcome": NOT_MEASURABLE, "reason": "CURRENT_CORE_NOT_MODELLED",
                "not_modelled": [c["id"] for c, _ in not_modelled]}

    def book(n_assets, core_idx):
        w = np.zeros(n_assets + 1)
        for s in sleeve_ids:
            w[[a["id"] for a in assets].index(s)] = sleeves_pct[s] / 100.0
        w[core_idx] = core_share
        w[-1] = cash
        return w

    def evaluate(c, col, rows_idx=None, with_tail=False):
        mat = np.column_stack([ils, col])
        if rows_idx is not None:
            mat = mat[rows_idx]
        a2 = assets + [{"id": f"core:{c['id']}", "kind": "core"}]
        est = fi.estimate(mat, a2, **est_kw)
        w = book(len(a2), len(a2) - 1)
        g = fr.growth(w, est["mu"], est["cov"], est["risk_free"])
        vol = fr.portfolio_stats(w, est["mu"], est["cov"])["volatility"]
        p = fr.goal_probability(nav, target, years, g["log_growth"], vol, monthly) * 100
        out = {"model_p_pct": round(p, 1), "growth_cagr_pct": round(g["cagr"] * 100, 2),
               "volatility_pct": round(vol * 100, 2),
               "fraction_of_kelly": fr.kelly_position(w, est["mu"], est["cov"], est["risk_free"])["fraction_of_kelly"]}
        if with_tail:
            daily = np.column_stack([paths.recentre(mat, est["mu"][:-1], vol_scale=est_kw.get("vol_scale")),
                                     np.full(mat.shape[0], est["risk_free"] / paths.TRADING_DAYS)])
            t = paths.goal_stats(paths.simulate(daily @ w, years=years), nav=nav, target=target,
                                 monthly=monthly, p_star=p_star)
            out.update({"tail_p_pct": t["p_goal_pct"], "worst5_mean": t["worst5_mean"],
                        "p_halve_pct": t["p_halve_pct"]})
            out["score_pct"] = min(out["model_p_pct"], out["tail_p_pct"])
        return out

    rows = []
    for c, col in modelled:
        r = {"id": c["id"], "name": c.get("name", c["id"]), "weights": c["weights"],
             **evaluate(c, col, with_tail=True)}
        rows.append(r)
    cur = next(r for r in rows if r["id"] == current_core_id)
    cur_k = cur["fraction_of_kelly"] or 0.0
    for r in rows:
        k = r["fraction_of_kelly"] or 0.0
        r["eligible"] = r["id"] == current_core_id or k <= max(ceiling, cur_k) + 1e-9

    rng = np.random.default_rng(seed)
    wins = {r["id"]: 0 for r in rows}
    eligible = [(c, col) for c, col in modelled if next(r for r in rows if r["id"] == c["id"])["eligible"]]
    for _ in range(draws):
        idx = block_bootstrap(ils.shape[0], BLOCK_DAYS, rng)
        scores = [(evaluate(c, col, rows_idx=idx)["model_p_pct"], c["id"]) for c, col in eligible]
        wins[max(scores)[1]] += 1
    for r in rows:
        r["win_share"] = round(wins[r["id"]] / draws, 3) if draws else None
        if r["fraction_of_kelly"] is not None:
            r["fraction_of_kelly"] = round(r["fraction_of_kelly"], 3)

    best = max((r for r in rows if r["eligible"]), key=lambda r: (r["score_pct"], r["model_p_pct"]))
    switch = (best["id"] != current_core_id
              and best["score_pct"] - cur["score_pct"] >= _material()
              and best["model_p_pct"] >= cur["model_p_pct"] and best["tail_p_pct"] >= cur["tail_p_pct"]
              and (best["win_share"] or 0) >= CORE_MIN_WIN_SHARE)
    return {"ok": True, "outcome": CORE_SWITCH if switch else CORE_KEEP,
            "current": cur, "best": best, "core_share_pct": round(core_share * 100, 2),
            "candidates": sorted(rows, key=lambda r: -r["score_pct"]),
            "not_modelled": [c["id"] for c, _ in not_modelled], "draws": draws,
            "rule": (f"score = lower of model and history odds; switch only if +{_material():g} pts, "
                     f"no worse on either, and best in >= {CORE_MIN_WIN_SHARE:.0%} of {draws} redraws")}


# --------------------------------------------------------------------------
# PR 96: protection tuned for the goal
# --------------------------------------------------------------------------
#
# PR 95 measured the user's 28 armed rules on the goal's paths: as the app
# executes them they cut the goal's odds from 32.9% to 0.5% (the sleeve stops'
# buy-back holds keep SOXL out 62% of days), while the drawdown guard alone
# does nearly all the crash protection. Eran, 2026-10-05: "continue to PR96" --
# turn that into a card that can act.
#
# PR 121 widened the search (owner, 2026-10-11: the card's "already the best"
# rested on two setups). A setup is:
#   - the sleeve exits (trailing stop, activated profit-protect, hard stop on
#     sleeve tickers) kept or paused;
#   - each sleeve's take-profits kept or paused;
#   - the drawdown guard's level x what it sells x how far under its level it
#     re-arms.
# Every value tried is a setting. Two passes: every structure at today's guard
# and every guard at today's structure, then every guard at the best structure.
# Each setup is screened on RETUNE_SCREEN_PATHS paths; the best few are
# re-measured on the full set, and the pick is the best goal odds among setups
# whose chance of HALVING stays <= HALVE_FLOOR -- protection from drops is a
# constraint the user set, not something to trade away. Retiring a rule PAUSES
# it; one tap on Holdings › Rules brings it back.

GUARD_LEVELS = (15.0, 17.5, 20.0, 25.0)
GUARD_FRACTIONS = (25.0, 50.0)
GUARD_REARM_GAPS = (2.5, 5.0)
RETUNE_SCREEN_PATHS = 250
RETUNE_FINALISTS = 3
RETUNE_MAX_SETUPS = 120
RULES_CARD_KIND = "frontier_rules"


def _guard_spec(lvl, g_spec, guard_col, fraction_pct=None, gap_pts=None) -> dict | None:
    """The drawdown guard at `lvl` percent, as `paths.simulate_rules` takes it.

    Sells `fraction_pct` and re-arms `gap_pts` under its level; each defaults to
    the guard as measured (`g_spec`), else the live settings.
    """
    from app.services.advanced_rules import derisk_fraction, rearm_gap_pts
    if lvl is None or guard_col is None:
        return None
    gap = gap_pts if gap_pts is not None else float((g_spec or {}).get("rearm_gap_pts", rearm_gap_pts()))
    frac = fraction_pct / 100.0 if fraction_pct is not None else (g_spec or {}).get("fraction", derisk_fraction())
    return {"level": lvl / 100.0, "rearm": max(lvl / 2.0, lvl - gap) / 100.0, "fraction": frac, "col": guard_col}


def _spec_id(s: dict, sleeve_ids) -> str:
    """A replay column's id: its sleeve, or (PR 123) the held ticker it models."""
    return s.get("id") or sleeve_ids[s["col"]]


def _setup_specs(c: dict, sl_specs, sleeve_ids, g_spec, guard_col):
    """One retune candidate -> the (sleeves, guard) `simulate_rules` takes."""
    off = set(c.get("pause_take_profits") or [])
    sl = [dict(s, **({"trail": None} if c["retire_sleeve_exits"] else {}),
               **({"tp_ratio": None} if _spec_id(s, sleeve_ids) in off else {})) for s in sl_specs]
    return sl, _guard_spec(c["guard_pct"], g_spec, guard_col, c.get("guard_fraction_pct"),
                           c.get("guard_rearm_gap_pts"))


def _fv_monthly(monthly: float, roi: float, years: float) -> float:
    """What `monthly` deposited at each month's end grows to at `roi` a year by
    the deadline -- the amount those deposits add to a "ROI on every shekel"
    goal (`goal_service.compound` on the deposits it would record)."""
    n = int(years * 12)
    return sum(monthly * (1.0 + roi) ** ((n - k) / 12.0) for k in range(1, n + 1))


PER_RULE_PATHS = 500


def _replay_without(run, rules: dict, sl_specs, g_spec, sleeve_ids, drop: set, n_paths: int) -> dict:
    """PR 124: the same replay with the rules in `drop` removed (ids). The guard
    goes too when no remaining rule arms one."""
    rows = [x for x in rules.get("_rule_rows") or [] if str(x.get("id")) not in drop]
    spec = rules_spec(rows, prices=rules.get("_prices") or {}, sleeve_tickers=rules.get("_sleeve_tickers") or {},
                      holding_tickers=set(rules.get("holdings") or {}))
    by_id = {**spec["sleeves"], **spec.get("holdings", {})}
    sl = []
    for s in sl_specs:
        sp = by_id.get(_spec_id(s, sleeve_ids)) or {}
        sl.append(dict(s, trail=sp.get("trail"), tp_ratio=sp.get("tp_ratio"), tp_fraction=sp.get("tp_fraction")))
    gd = None if (g_spec is None or spec["guard"] is None) else {**spec["guard"], "col": g_spec["col"]}
    return run(sl=sl, gd=gd, n_paths=n_paths)


def _per_rule_impact(run, rules: dict, sl_specs, g_spec, sleeve_ids) -> dict | None:
    """PR 123 (D85): each rule's own effect on the goal -- the replay with that
    one rule switched off, on the same paths as every rule on.

    ``goal_pts`` = chance with the rule minus chance without it (negative: the
    rule costs the goal); the halving chance with and without beside it. A rule
    another rule on the same holding already covers (a looser stop beside a
    tighter one) changes nothing in the replay and says so. Rules the model does
    not replay carry their reason from `_rules_context`.
    """
    rows = rules.get("_rule_rows")
    if not rows:
        return None
    base = run(n_paths=PER_RULE_PATHS)
    out: dict = {}
    ids = [_spec_id(s, sleeve_ids) for s in sl_specs]
    for r in rows:
        rid = str(r.get("id") or "")
        why = (rules.get("unmodelled") or {}).get(rid)
        if not rid or why:
            if rid:
                out[rid] = {"modelled": False, "reason": why}
            continue
        if r.get("rule_type") == "drawdown_guard":
            if g_spec is None:
                out[rid] = {"modelled": False, "reason": "the guard needs a strategy sleeve to sell"}
                continue
            sl, gd = None, None
        else:
            spec = rules_spec([x for x in rows if x is not r], prices=rules.get("_prices") or {},
                              sleeve_tickers=rules.get("_sleeve_tickers") or {},
                              holding_tickers=set(rules.get("holdings") or {}))
            by_id = {**spec["sleeves"], **spec.get("holdings", {})}
            sl = []
            for s, sid in zip(sl_specs, ids):
                sp = by_id.get(sid) or {}
                sl.append(dict(s, trail=sp.get("trail"), tp_ratio=sp.get("tp_ratio"),
                               tp_fraction=sp.get("tp_fraction")))
            if all(a.get("trail") == b.get("trail") and a.get("tp_ratio") == b.get("tp_ratio")
                   for a, b in zip(sl, sl_specs)):
                out[rid] = {"modelled": True, "covered": True, "goal_pts": 0.0,
                            "halve_with_pct": base["p_halve_pct"], "halve_without_pct": base["p_halve_pct"]}
                continue
            gd = "same"
        without = run(sl=sl, gd=gd, n_paths=PER_RULE_PATHS)
        out[rid] = {"modelled": True, "covered": False,
                    "goal_pts": round(base["p_goal_pct"] - without["p_goal_pct"], 1),
                    "halve_with_pct": base["p_halve_pct"], "halve_without_pct": without["p_halve_pct"]}
    return {"paths": PER_RULE_PATHS, "base_goal_pct": base["p_goal_pct"], "rules": out}


def _tune_protection(run, sl_specs, g_spec, rules, sleeve_ids, unit_daily, k, r_on,
                     floor: float = HALVE_FLOOR_PCT) -> dict | None:
    from app.services import thresholds
    from app.services.advanced_rules import derisk_fraction, rearm_gap_pts
    has_exits = any(s.get("trail") for s in sl_specs)
    tp_sids = [_spec_id(s, sleeve_ids) for s in sl_specs if s.get("tp_ratio")]
    if not has_exits and not g_spec and not tp_sids:
        return None
    cur_guard = round(g_spec["level"] * 100, 2) if g_spec else None
    cur_frac = round(float((g_spec or {}).get("fraction", derisk_fraction())) * 100, 2)
    cur_gap = round(float((g_spec or {}).get("rearm_gap_pts", rearm_gap_pts())), 2)
    guard_col = g_spec["col"] if g_spec else (int(np.argmax(unit_daily[:, :k].std(axis=0))) if k else None)

    def axis(cur, key, default):                      # today's value first, then the setting's
        tried = [round(float(x), 2) for x in thresholds.get(key, default)]
        return ([] if cur is None else [cur]) + [x for x in tried if x != cur]
    fracs = axis(cur_frac, "guard_fractions_tried", GUARD_FRACTIONS)
    gaps = axis(cur_gap, "guard_rearm_gaps_tried", GUARD_REARM_GAPS)
    levels = axis(cur_guard, "guard_levels", GUARD_LEVELS)
    if cur_guard is None:
        levels = [None] + levels
    guards = []
    for lvl in levels:
        if lvl is None:
            guards.append((None, None, None))
        else:
            guards += [(lvl, f, gp) for f in fracs for gp in gaps]
    tp_opts = [()] + ([tuple(tp_sids)] if tp_sids else []) + ([(x,) for x in tp_sids] if len(tp_sids) > 1 else [])
    structs = [(r, tp) for r in ((False, True) if has_exits else (False,)) for tp in tp_opts]
    cur_key = (structs[0], guards[0])

    def cand(key):
        (retire, tp_off), (lvl, f, gp) = key
        return {"retire_sleeve_exits": retire, "pause_take_profits": list(tp_off), "guard_pct": lvl,
                "guard_fraction_pct": f, "guard_rearm_gap_pts": gp, "current": key == cur_key}

    screened: dict = {}

    def screen(key):
        if key in screened or len(screened) >= RETUNE_MAX_SETUPS:
            return
        c = cand(key)
        sl, gd = _setup_specs(c, sl_specs, sleeve_ids, g_spec, guard_col)
        screened[key] = {**c, "stats": run(sl=sl, gd=gd, n_paths=RETUNE_SCREEN_PATHS),
                         "paths": RETUNE_SCREEN_PATHS}

    def rank(c):
        return (c["stats"]["p_goal_pct"], -c["stats"]["p_halve_pct"])
    for st in structs:                                   # pass 1
        screen((st, guards[0]))
    for g in guards:
        screen((structs[0], g))
    first = [c for c in screened.values() if c["stats"]["p_halve_pct"] <= floor]
    if first:                                            # pass 2: every guard at the best structure
        b = max(first, key=rank)
        best_st = (b["retire_sleeve_exits"], tuple(b["pause_take_profits"]))
        for g in guards:
            screen((best_st, g))
    # The finalists, and today's setup, on the full set of paths.
    finalists = sorted((c for key, c in screened.items()
                        if key != cur_key and c["stats"]["p_halve_pct"] <= floor),
                       key=rank, reverse=True)[:RETUNE_FINALISTS]
    for c in finalists:
        sl, gd = _setup_specs(c, sl_specs, sleeve_ids, g_spec, guard_col)
        c["stats"], c["paths"] = run(sl=sl, gd=gd), None
    cur_c = screened.get(cur_key) or cand(cur_key)
    cur_c["stats"], cur_c["paths"] = r_on, None
    screened[cur_key] = cur_c
    ok = [c for c in screened.values() if c["paths"] is None and c["stats"]["p_halve_pct"] <= floor]
    best = max(ok, key=rank) if ok else None
    # D83 (owner, 2026-10-12): the halving limit is a HARD limit. When today's
    # rules break it, the best setup inside it is offered even if it costs goal
    # odds -- the card states the cost; the owner decides.
    breach = r_on["p_halve_pct"] > floor
    material = bool(best and not best["current"]
                    and (breach or best["stats"]["p_goal_pct"] - r_on["p_goal_pct"] >= _material()))
    changes = {"pause_rule_ids": [], "pause_rules": [], "guard": None, "guard_settings": None}
    if material:
        by_id = {**(rules.get("sleeves") or {}), **(rules.get("holdings") or {})}
        for sid in [_spec_id(s, sleeve_ids) for s in sl_specs]:
            sp = by_id.get(sid) or {}
            if best["retire_sleeve_exits"]:
                changes["pause_rule_ids"] += sp.get("exit_rule_ids") or []
                changes["pause_rules"] += [d for d in sp.get("rules") or [] if not d.startswith("take_profit")]
            if sid in best["pause_take_profits"]:
                changes["pause_rule_ids"] += sp.get("tp_rule_ids") or []
                changes["pause_rules"] += [d for d in sp.get("rules") or [] if d.startswith("take_profit")]
        if best["guard_pct"] != cur_guard:
            changes["guard"] = {"rule_id": (rules.get("guard") or {}).get("rule_id"),
                                "from_pct": cur_guard, "to_pct": best["guard_pct"]}
        if best["guard_pct"] is not None and (best["guard_fraction_pct"], best["guard_rearm_gap_pts"]) != (
                cur_frac, cur_gap):
            changes["guard_settings"] = {"from": {"fraction_pct": cur_frac, "rearm_gap_pts": cur_gap},
                                         "to": {"fraction_pct": best["guard_fraction_pct"],
                                                "rearm_gap_pts": best["guard_rearm_gap_pts"]}}
    cands = list(screened.values())
    return {"halve_floor_pct": floor, "material": material, "guard_col": guard_col, "breach": breach,
            "current": {"guard_pct": cur_guard, "guard_fraction_pct": cur_frac, "guard_rearm_gap_pts": cur_gap,
                        "stats": r_on},
            "best": best, "changes": changes if material else None,
            # What the sentences may claim: how many setups were measured, and
            # whether pausing a take-profit was one of them.
            "tried": len(cands), "take_profits_measured": bool(tp_sids),
            "screen_paths": RETUNE_SCREEN_PATHS, "candidates": cands}


def plan_trades(verdict: dict, *, specs: dict[str, dict], holdings: list[dict]) -> dict:
    """The trades a RESIZE verdict implies for today's holdings. Pure.

    ``specs`` maps sleeve id -> its catalog spec; ``holdings`` is
    ``[{"ticker", "shares", "value_ils", "tax_ils"}]`` per held row (value and
    tax for selling the WHOLE row). Each resized sleeve sells the same fraction
    of every ticker it can hold -- the share of the sleeve that is going away.

    A ticker two sleeves can both hold is not split by guesswork: that sleeve
    is skipped and the reason travels on the plan.
    """
    from app.engines import blend
    from app.services.funding_service import MIN_TRADE_ILS

    claim = {sid: {t.upper() for t in blend.claimable_tickers(spec)} for sid, spec in specs.items()}
    resizes, sells, skipped = [], [], []
    for s in verdict.get("sleeves") or []:
        if not s.get("act"):
            continue
        sid = s["id"]
        mine = claim.get(sid) or set()
        shared = sorted(t for t in mine for o, ts in claim.items() if o != sid and t in ts)
        if shared:
            skipped.append({"id": sid, "reason": "SHARED_TICKER", "tickers": shared})
            continue
        if s["proposed_pct"] > s["current_pct"]:
            # PR 92: a GROWING sleeve sells nothing of its own. Accept funds it
            # through `strategy_service.load_basket` (mode "fund"), the one path
            # that obeys "funding may only sell what the plan has too much of".
            resizes.append({"strategy_id": sid, "from_pct": s["current_pct"],
                            "to_pct": s["proposed_pct"], "fraction_sold": 0.0,
                            "direction": "grow", "tickers": sorted(mine)})
            continue
        frac = 1.0 - s["proposed_pct"] / s["current_pct"]
        resizes.append({"strategy_id": sid, "from_pct": s["current_pct"],
                        "to_pct": s["proposed_pct"], "fraction_sold": round(frac, 6),
                        "direction": "shrink", "tickers": sorted(mine)})
        for h in holdings:
            if h["ticker"].upper() in mine and h["shares"] > 0:
                sells.append({"ticker": h["ticker"].upper(), "strategy_id": sid,
                              "shares": round(h["shares"] * frac, 6),
                              "value_ils": round(h["value_ils"] * frac, 2),
                              "tax_ils": round(h["tax_ils"] * frac, 2)})
    gross = sum(x["value_ils"] for x in sells)
    tax = sum(x["tax_ils"] for x in sells)
    net = gross - tax
    basket = {t.upper(): w for t, w in (verdict.get("core_basket") or {}).items()
              if w > 0 and t.upper() != "CASH"}
    total_w = sum(basket.values())
    buys = []
    # When a sleeve also GROWS, the shrink's proceeds stay as cash for it:
    # buying the core first only for the grow's funding to sell it again is two
    # taxable legs that cancel.
    grows = any(r.get("direction") == "grow" for r in resizes)
    for tk, w in sorted(basket.items()):
        amt = round(net * w / total_w, 2) if total_w and not grows else 0.0
        if amt >= MIN_TRADE_ILS:
            buys.append({"ticker": tk, "weight": round(w / total_w, 6), "amount_ils": amt})
    return {"resizes": resizes, "sells": sells, "buys": buys, "skipped": skipped,
            "gross_ils": round(gross, 2), "tax_ils": round(tax, 2), "net_ils": round(net, 2),
            "kept_in_cash_ils": round(net - sum(b["amount_ils"] for b in buys), 2)}


# --------------------------------------------------------------------------
# the store
# --------------------------------------------------------------------------

async def record(session, user, verdict: dict, *, source: str = "on_demand"):
    """Persist one frontier verdict. Best-effort, like `solver_store.record`."""
    from app.models.tables import SolverVerdict
    if not isinstance(verdict, dict) or not verdict.get("outcome"):
        return None
    try:
        row = SolverVerdict(subject=user.email, solver_version=FRONTIER_TAG,
                            outcome=str(verdict["outcome"])[:32], target_excess_pct=None,
                            max_drawdown_pct=None, solved_total_sleeve_pct=None,
                            floor_breaches_ceiling=False, binding_constraint="frontier",
                            source=source[:16], detail=verdict,
                            # Microseconds, set here: `latest()` orders by this, and
                            # the database default (SQLite: whole seconds) let two
                            # verdicts in one second come back in either order -- a
                            # re-solve right after another could serve the older.
                            created_at=datetime.now(timezone.utc))
        session.add(row)
        await session.commit()
        return row
    except Exception:  # noqa: BLE001 -- a solve that answered must not fail on its receipt
        logger.warning("could not record frontier verdict", exc_info=False)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


async def latest(session, user):
    from sqlalchemy import desc, select

    from app.models.tables import SolverVerdict
    return (await session.scalars(
        select(SolverVerdict).where(SolverVerdict.subject == user.email)
        .where(SolverVerdict.solver_version == FRONTIER_TAG)
        .order_by(desc(SolverVerdict.created_at)).limit(1))).first()


def _age_hours(row) -> float | None:
    ts = getattr(row, "created_at", None)
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0


# --------------------------------------------------------------------------
# the loader
# --------------------------------------------------------------------------

async def _core_basket(session, user, objective: str) -> dict[str, float]:
    """The basket the core BUYS: the chosen core strategy's, else the objective's."""
    from app.services import sleeve_service as sv
    from app.services import strategies as static_cat
    from app.services.allocation_mix import OBJ_TARGET
    sid = await sv.core_strategy_id(session, user)
    basket = static_cat.core_weights(sid) if sid else None
    if basket:
        return basket
    default = {"Equities": "VTI", "Fixed Income": "BND", "Commodities": "IAU", "Cash": "CASH"}
    return {default[c]: w for c, w in (OBJ_TARGET.get(objective) or {}).items() if c in default}


async def compute_for(session, user, *, source: str = "on_demand",
                      draws: int = DRAWS) -> dict:
    """Solve for the acting user's book and record the verdict. Read-only on the book."""
    from app.core.offload import offload
    from app.services.allocation_mix import current_mix
    from app.services.intake_service import list_positions

    import asyncio

    ctx = await fi.load_context(session, user)
    if not ctx.get("ok"):
        return ctx
    # A COLD CACHE IS NOT A BOOK WITH NO PRICES. Measured on production right
    # after PR 90's deploy: every ticker came back "unavailable" (rate limited
    # / circuit open), the inputs reported NO_ASSETS, and 30s later the same
    # call priced everything. One wait past the breaker's recovery, one retry;
    # still unavailable is stored as UNAVAILABLE, never as NOT_MEASURABLE.
    if ctx.get("unavailable") and fi.prepare(ctx["series"], sleeves=ctx["sleeves"]).get("reason") == "NO_ASSETS":
        await asyncio.sleep(RETRY_AFTER_S)
        ctx = await fi.load_context(session, user)
        if not ctx.get("ok"):
            return ctx
    rows = await list_positions(session, user)
    mix, _nav = current_mix(rows)
    cash_pct = float(mix.get("Cash") or 0.0) * 100.0
    basket = await _core_basket(session, user, ctx["objective"])
    goal = await _goal_context(session, user, rows)
    from app.services import thresholds as _thr
    from app.services.plan_service import get_plan as _gp
    _plan_now = await _gp(session, user)
    _thr.use(_plan_now)                     # PR 104d: the worker threads inherit this
    _st = settings_of(_plan_now)
    sleeves_pct = {s["id"]: float(s["current_pct"]) for s in ctx["sleeves"]}
    from app.services import sleeve_service as _sv
    from app.services import strategies as _static_cat
    current_core = await _sv.core_strategy_id(session, user)
    core_candidates = _static_cat.core_candidates()
    try:
        rules_ctx = await _rules_context(session, user, ctx["sleeves"])
    except Exception:  # noqa: BLE001 -- the goal verdict stands without the rules replay
        logger.warning("rules context failed", exc_info=True)
        rules_ctx = None
    try:
        _hseries = {**ctx["series"], **await _holding_series(rules_ctx, ctx["series"])}
    except Exception:  # noqa: BLE001 -- held tickers then go unmodelled, said so
        logger.warning("held-ticker history unavailable", exc_info=False)
        _hseries = ctx["series"]

    def work():
        prep = fi.prepare(ctx["series"], sleeves=ctx["sleeves"])
        if prep["ok"]:
            _attach_holding_returns(rules_ctx, prep, _hseries)
        if not prep["ok"]:
            unavailable = prep.get("reason") == "NO_ASSETS" and bool(ctx.get("unavailable"))
            return {"ok": True, "outcome": UNAVAILABLE if unavailable else NOT_MEASURABLE,
                    "reason": prep.get("reason"), "detail": prep.get("detail"),
                    "unavailable": ctx.get("unavailable")}
        # PR 97: today's volatility level, weighted by the regime, everywhere.
        vs = fi.vol_scale(prep, (ctx.get("market") or {}).get("state"))
        est_kw = {**ctx["est_kw"], "vol_scale": vs["scale"]}
        v = _work_solve(prep, est_kw)
        if isinstance(v, dict):
            v["market"] = {**(ctx.get("market") or {}), "recent_weight": vs["recent_weight"],
                           "vol": vs["assets"], "halflife_days": fi.RECENT_HALFLIFE_DAYS}
        return v

    def _work_solve(prep, est_kw):
        if goal:
            # PR 92: the goal is the objective whenever the plan states one.
            v = goal_solve(prep, sleeves_pct=sleeves_pct, core_basket=basket, cash_pct=cash_pct,
                           objective=ctx["objective"], est_kw=est_kw, nav=goal["nav"],
                           target=goal["target"], years=goal["years"], monthly=goal["monthly"],
                           kelly_setting=ctx.get("kelly_fraction_setting"),
                           sleeve_cap_pct=goal["sleeve_cap_pct"],
                           volatility_cap_pct=goal["volatility_cap_pct"],
                           draws=min(draws, GOAL_DRAWS), rules=rules_ctx,
                           goal_roi=goal.get("roi_annual"), p_star=_st["goal_confidence"],
                           halve_floor_pct=_st["halve_limit_pct"])
            # PR 94: and which CORE serves the goal, the sleeves as they are.
            if v.get("mode") == "goal" and v.get("outcome") not in (NOT_MEASURABLE, GOAL_NO_TARGET) and current_core:
                try:
                    v["core_choice"] = core_solve(
                        prep, sleeves_pct=sleeves_pct, current_core_id=current_core,
                        candidates=core_candidates, cash_pct=cash_pct, est_kw=est_kw,
                        nav=goal["nav"], target=goal["target"], years=goal["years"],
                        monthly=goal["monthly"], objective=ctx["objective"],
                        kelly_setting=ctx.get("kelly_fraction_setting"), p_star=_st["goal_confidence"])
                except Exception:  # noqa: BLE001 -- the goal verdict stands without it
                    logger.warning("core choice failed", exc_info=True)
            return v
        # No goal on the plan: manage risk to the Kelly dial (PR 91's resize).
        return resize_solve(prep, sleeves_pct=sleeves_pct, core_basket=basket, cash_pct=cash_pct,
                            objective=ctx["objective"], est_kw=est_kw,
                            kelly_fraction=ctx.get("kelly_fraction_setting"),
                            target_roi_pct=ctx.get("target_roi_pct"), draws=draws)

    out = await offload(work)
    if goal and out.get("mode") == "goal":
        amt = (out.get("levers") or {}).get("amount_at_confidence")
        if amt:
            out["levers"]["roi_at_confidence_pct"] = _roi_for_amount(goal, float(amt))
        out["goal"]["deadline"] = goal.get("deadline")
        out["goal"]["roi_pct"] = goal.get("roi_pct")
        out["goal"]["target_date_raw"] = goal.get("target_date_raw")
    out["computed_at"] = datetime.now(timezone.utc).isoformat()
    out["version"] = FRONTIER_TAG
    try:
        out["rules_fp"] = await rules_fingerprint(session, user)
    except Exception:  # noqa: BLE001 -- a verdict without one is judged as before
        logger.warning("rules fingerprint unavailable", exc_info=False)
    await record(session, user, out, source=source)
    return out


def rules_spec(rules: list[dict], *, prices: dict[str, float],
               sleeve_tickers: dict[str, set[str]], holding_tickers: set[str] | None = None) -> dict:
    """The user's ARMED rules -> what `paths.simulate_rules` takes. Pure.

    ``rules`` are rows as dicts {ticker, rule_type, mode, level, params,
    trim_fraction}; ``prices`` today's price per ticker (the rules' own
    currency); ``sleeve_tickers`` the tickers each sleeve can hold. Per sleeve
    the TIGHTEST exit wins (trailing stop, activated profit-protect, or a hard
    stop expressed as a fall from today's price) and the NEAREST take-profit.
    """
    from app.services.advanced_rules import derisk_fraction, guard_rearm_pct, rearm_gap_pts
    from app.services.rules_service import TAKE_PROFIT_FRACTION

    def _blank():
        return {"trail": None, "tp_ratio": None, "tp_fraction": TAKE_PROFIT_FRACTION, "rules": [],
                "exit_rule_ids": [], "tp_rule_ids": []}
    out = {sid: _blank() for sid in sleeve_tickers}
    # PR 123: a held ticker outside every sleeve, modelled as its own column.
    held = {t.upper() for t in (holding_tickers or set())}
    out_h: dict = {}
    guard, not_sim = None, []
    for r in rules:
        tk, rt = (r.get("ticker") or "").upper(), r.get("rule_type")
        lvl = float(r.get("level") or 0.0)
        if rt == "drawdown_guard" and tk == "__BOOK__" and lvl > 0:
            guard = {"level": lvl / 100.0, "rearm": guard_rearm_pct(lvl) / 100.0,
                     "fraction": derisk_fraction(), "rearm_gap_pts": rearm_gap_pts()}
            if r.get("id"):
                guard["rule_id"] = str(r["id"])
            continue
        sid = next((s for s, ts in sleeve_tickers.items() if tk in ts), None)
        if sid is None and tk in held:
            spec = out_h.setdefault(tk, _blank())
        elif sid is None:
            if rt in ("stop_loss", "trailing_stop", "profit_protect", "take_profit", "drawdown_guard"):
                not_sim.append(f"{rt} on {tk}")
            continue
        else:
            spec = out[sid]
        px = float(prices.get(tk) or 0.0)
        trail = None
        if rt == "trailing_stop" and r.get("mode", "pct") == "pct":
            trail = lvl / 100.0
        elif rt == "profit_protect" and (r.get("params") or {}).get("activated"):
            trail = lvl / 100.0
        elif rt == "stop_loss":
            trail = (1.0 - lvl / px) if r.get("mode") == "price" and px > 0 else lvl / 100.0
        if trail is not None and 0 < trail < 1:
            spec["trail"] = trail if spec["trail"] is None else min(spec["trail"], trail)
            spec["rules"].append(f"{rt} {tk} ({trail:.0%} below the peak)")
            if r.get("id"):
                spec["exit_rule_ids"].append(str(r["id"]))
        if rt == "take_profit" and px > 0:
            ratio = lvl / px if r.get("mode") == "price" else 1.0 + lvl / 100.0
            if ratio > 1.0:
                spec["tp_ratio"] = ratio if spec["tp_ratio"] is None else min(spec["tp_ratio"], ratio)
                frac = r.get("trim_fraction")
                spec["tp_fraction"] = float(frac) if frac and 0 < float(frac) <= 1 else TAKE_PROFIT_FRACTION
                spec["rules"].append(f"take_profit {tk} (+{ratio - 1:.0%}, sells {spec['tp_fraction']:.0%})")
                if r.get("id"):
                    spec["tp_rule_ids"].append(str(r["id"]))
    return {"sleeves": out, "holdings": out_h, "guard": guard, "not_simulated": sorted(set(not_sim))}


async def preview(session, user, *, roi_pct: float | None = None, target_date: str | None = None,
                  monthly: float | None = None, armed: dict | None = None,
                  guard_pct: float | None = None, extra_rules: list[dict] | None = None) -> dict:
    """PR 104b: the goal's odds as they WOULD be -- another target, deadline or
    deposit (Edit goal), or other rules armed (Edit rules). The same model and
    rules replay as the nightly solve, light: no resampling, no retune search,
    no stress case, no lever search. Read-only: nothing is stored.
    """
    from app.core.offload import offload
    from app.services.allocation_mix import current_mix
    from app.services.intake_service import list_positions

    ctx = await fi.load_context(session, user)
    if not ctx.get("ok"):
        return {"ok": False, "reason": ctx.get("reason") or "NO_INPUTS"}
    rows = await list_positions(session, user)
    mix, _nav = current_mix(rows)
    cash_pct = float(mix.get("Cash") or 0.0) * 100.0
    basket = await _core_basket(session, user, ctx["objective"])
    goal = await _goal_context(session, user, rows, roi_pct=roi_pct, target_date=target_date, monthly=monthly)
    if not goal:
        return {"ok": False, "reason": "NO_GOAL"}
    sleeves_pct = {s["id"]: float(s["current_pct"]) for s in ctx["sleeves"]}
    rules_ctx = await _rules_context(session, user, ctx["sleeves"], armed=armed, guard_pct=guard_pct,
                                     extra=extra_rules)
    if extra_rules and rules_ctx:
        rules_ctx["_compare_drop"] = [f"new:{i}" for i in range(len(extra_rules))]
    try:
        _hseries = {**ctx["series"], **await _holding_series(rules_ctx, ctx["series"])}
    except Exception:  # noqa: BLE001
        _hseries = ctx["series"]
    from app.services import thresholds as _thr
    from app.services.plan_service import get_plan as _gp
    _plan_now = await _gp(session, user)
    _thr.use(_plan_now)
    _st = settings_of(_plan_now)

    def work():
        prep = fi.prepare(ctx["series"], sleeves=ctx["sleeves"])
        if not prep["ok"]:
            return {"ok": False, "reason": prep.get("reason")}
        _attach_holding_returns(rules_ctx, prep, _hseries)
        vs = fi.vol_scale(prep, (ctx.get("market") or {}).get("state"))
        return goal_solve(prep, sleeves_pct=sleeves_pct, core_basket=basket, cash_pct=cash_pct,
                          objective=ctx["objective"], est_kw={**ctx["est_kw"], "vol_scale": vs["scale"]},
                          nav=goal["nav"], target=goal["target"], years=goal["years"], monthly=goal["monthly"],
                          kelly_setting=ctx.get("kelly_fraction_setting"), sleeve_cap_pct=goal["sleeve_cap_pct"],
                          volatility_cap_pct=goal["volatility_cap_pct"], draws=0, rules=rules_ctx,
                          goal_roi=goal.get("roi_annual"), light=True, p_star=_st["goal_confidence"])

    v = await offload(work)
    if not v.get("ok") or v.get("mode") != "goal" or "before" not in v:
        return {"ok": False, "reason": v.get("reason") or v.get("outcome") or "NOT_MEASURABLE"}
    rb = v.get("rules") or {}
    on, off = rb.get("on") or {}, rb.get("off") or {}
    return {"ok": True,
            "goal": {"target": round(goal["target"], 0), "deadline": goal.get("deadline"),
                     "monthly": round(goal["monthly"], 2), "years": round(goal["years"], 2),
                     "roi_pct": goal.get("roi_pct")},
            "headline": v.get("headline"),
            "p_halve_pct": on.get("p_halve_pct", (v.get("tail") or {}).get("before", {}).get("p_halve_pct")),
            "p_halve_without_rules_pct": off.get("p_halve_pct"),
            "median": on.get("median"), "worst5_mean": on.get("worst5_mean"),
            # PR 124: what the proposed rules change, and which of them the model replays.
            "compare": rb.get("compare"),
            "proposed": [{"ticker": (x.get("ticker") or "").upper(), "rule_type": x.get("rule_type"),
                          "level": x.get("level"),
                          "modelled": f"new:{i}" not in ((rules_ctx or {}).get("unmodelled") or {}),
                          "reason": ((rules_ctx or {}).get("unmodelled") or {}).get(f"new:{i}")}
                         for i, x in enumerate(extra_rules or [])]}


async def score_books(session, user, books: list[dict[str, float]], *,
                      extra_sleeves: list[str] | None = None, nominal_nav: float | None = None,
                      full: bool = False) -> dict:
    """PR 135a: the goal's chance for each of several HYPOTHETICAL sleeve sets.

    ``books`` are ``{strategy_id: sleeve_pct}`` maps; a sleeve left out is removed
    (its room returns to the core). ``extra_sleeves`` names catalogue strategies not
    held that some book adds. Everything is loaded once and every book is scored by
    the light goal solve `preview` uses -- same model, same rules replay, no
    resampling -- so the numbers compare with each other and with the goal card.
    Read-only: nothing is stored here.
    """
    from app.core.offload import offload
    from app.services.allocation_mix import current_mix
    from app.services.intake_service import list_positions

    ctx = await fi.load_context(session, user, extra_sleeves=extra_sleeves)
    if not ctx.get("ok"):
        return {"ok": False, "reason": ctx.get("reason") or "NO_INPUTS"}
    rows = await list_positions(session, user)
    mix, _nav = current_mix(rows)
    cash_pct = float(mix.get("Cash") or 0.0) * 100.0
    basket = await _core_basket(session, user, ctx["objective"])
    goal = await _goal_context(session, user, rows, nominal_nav=nominal_nav)
    if not goal:
        return {"ok": False, "reason": "NO_GOAL"}
    from app.services import sleeve_service as _sv
    from app.services import strategies as _static_cat
    current_core = await _sv.core_strategy_id(session, user) if full else None
    rules_ctx = await _rules_context(session, user, ctx["sleeves"])
    try:
        _hseries = {**ctx["series"], **await _holding_series(rules_ctx, ctx["series"])}
    except Exception:  # noqa: BLE001
        _hseries = ctx["series"]
    from app.services import thresholds as _thr
    from app.services.plan_service import get_plan as _gp
    _plan_now = await _gp(session, user)
    _thr.use(_plan_now)
    _st = settings_of(_plan_now)

    def work():
        prep = fi.prepare(ctx["series"], sleeves=ctx["sleeves"])
        if not prep["ok"]:
            return {"ok": False, "reason": prep.get("reason")}
        _attach_holding_returns(rules_ctx, prep, _hseries)
        vs = fi.vol_scale(prep, (ctx.get("market") or {}).get("state"))
        modelled = {a["id"] for a in prep["assets"] if a["kind"] == "sleeve"}
        out = []
        for b in books:
            missing = sorted(sid for sid, pct in b.items() if pct > 0 and sid not in modelled)
            if missing:
                out.append({"ok": False, "reason": "NOT_MODELLED", "detail": f"no model for {', '.join(missing)}"})
                continue
            v = goal_solve(prep, sleeves_pct={k: float(p) for k, p in b.items() if p > 0},
                           core_basket=basket, cash_pct=cash_pct, objective=ctx["objective"],
                           est_kw={**ctx["est_kw"], "vol_scale": vs["scale"]},
                           nav=goal["nav"], target=goal["target"], years=goal["years"], monthly=goal["monthly"],
                           kelly_setting=ctx.get("kelly_fraction_setting"), sleeve_cap_pct=goal["sleeve_cap_pct"],
                           volatility_cap_pct=goal["volatility_cap_pct"], draws=0, rules=rules_ctx,
                           goal_roi=goal.get("roi_annual"), light=not full, p_star=_st["goal_confidence"],
                           halve_floor_pct=_st["halve_limit_pct"])
            if not v.get("ok") or v.get("mode") != "goal" or "before" not in v:
                out.append({"ok": False, "reason": v.get("reason") or v.get("outcome") or "NOT_MEASURABLE"})
                continue
            rb = v.get("rules") or {}
            on = rb.get("on") or {}
            hl = v.get("headline") or {}
            row = {"ok": True, "p_goal_pct": hl.get("p_goal_pct"),
                   "p_goal_without_rules_pct": hl.get("without_rules_pct"),
                   # PR 136b: the price of the protection -- halving with no rule acting.
                   "p_halve_without_rules_pct": (rb.get("off") or {}).get("p_halve_pct"),
                   "p_halve_pct": on.get("p_halve_pct", (v.get("tail") or {}).get("before", {}).get("p_halve_pct")),
                   "median": on.get("median"),
                   "growth_cagr_pct": (v.get("before") or {}).get("growth_cagr_pct"),
                   # PR 136a: the sizes the goal model would choose for THIS set of sleeves.
                   "proposed": {s["id"]: s.get("proposed_pct") for s in v.get("sleeves") or []
                                if s.get("proposed_pct") is not None}}
            if full:
                # PR 136a step B: the protection retune and the core choice, on this book.
                tu = rb.get("tuning") or {}
                best = tu.get("best") or {}
                row["protection"] = {"material": tu.get("material"), "breach": tu.get("breach"),
                                     "halve_floor_pct": tu.get("halve_floor_pct"),
                                     "best_p_goal_pct": (best.get("stats") or {}).get("p_goal_pct"),
                                     "best_p_halve_pct": (best.get("stats") or {}).get("p_halve_pct"),
                                     "changes": tu.get("changes")} if tu else None
                if current_core:
                    try:
                        cs = core_solve(prep, sleeves_pct={k: float(p) for k, p in b.items() if p > 0},
                                        current_core_id=current_core, candidates=_static_cat.core_candidates(),
                                        cash_pct=cash_pct, est_kw={**ctx["est_kw"], "vol_scale": vs["scale"]},
                                        nav=goal["nav"], target=goal["target"], years=goal["years"],
                                        monthly=goal["monthly"], objective=ctx["objective"],
                                        kelly_setting=ctx.get("kelly_fraction_setting"),
                                        p_star=_st["goal_confidence"])
                        best_c = cs.get("best") or {}
                        row["core"] = {"outcome": cs.get("outcome"), "current": current_core,
                                       "best": best_c.get("id"), "best_name": best_c.get("name")}
                    except Exception:  # noqa: BLE001 -- the book stands without the core choice
                        logger.warning("core choice failed in score_books", exc_info=False)
            out.append(row)
        return {"ok": True, "scores": out}

    return await offload(work)


async def _rules_context(session, user, sleeves: list[dict], *, armed: dict | None = None,
                         guard_pct: float | None = None, extra: list[dict] | None = None) -> dict | None:
    """The user's active rules and today's prices, mapped onto his sleeves.

    PR 104b: `armed` ({rule id: bool}) and `guard_pct` describe the rules as they
    WOULD be -- the Edit-rules sheet's preview -- without saving anything.
    """
    from sqlalchemy import select

    from app.engines import blend
    from app.models.tables import TradingRule
    from app.services.intake_service import list_positions

    every = (await session.scalars(select(TradingRule).where(TradingRule.subject == user.email))).all()
    rows = [r for r in every if (armed or {}).get(str(r.id), bool(r.active))]
    if not rows and not extra:
        return None
    from app.services.allocation_mix import current_mix
    from app.services.fx import position_value_ils, price_currency
    from app.services.intake_service import is_cash_position
    positions = await list_positions(session, user)
    _mix, nav = current_mix(positions)
    prices, values, ccys = {}, {}, {}
    for p in positions:
        tk = (p.ticker or "").upper()
        meta = p.meta if isinstance(p.meta, dict) else None
        if p.current_price:
            prices[tk] = float(p.current_price)
        if not is_cash_position(tk, meta) and float(p.quantity or 0) > 0:
            values[tk] = values.get(tk, 0.0) + position_value_ils(p)
            ccys[tk] = price_currency(p.market, meta)
    tickers = {s["id"]: {t.upper() for t in blend.claimable_tickers(s["spec"])} for s in sleeves}
    in_sleeve = set().union(*tickers.values()) if tickers else set()
    dicts = [{"id": str(r.id), "ticker": r.ticker, "rule_type": r.rule_type, "mode": r.mode,
              "level": (float(guard_pct) if guard_pct is not None and r.rule_type == "drawdown_guard"
                        else r.level),
              "params": r.params, "trim_fraction": getattr(r, "trim_fraction", None)} for r in rows]
    # PR 124: rules a card PROPOSES, as if armed (ids "new:<n>"); nothing is saved.
    for i, x in enumerate(extra or []):
        dicts.append({"id": f"new:{i}", "ticker": (x.get("ticker") or "").upper(), "rule_type": x.get("rule_type"),
                      "mode": x.get("mode") or "pct", "level": float(x.get("level") or 0.0),
                      "params": x.get("params"), "trim_fraction": x.get("trim_fraction")})
    # PR 123 (D85): every HELD ticker outside the sleeves that carries a sale rule
    # becomes its own column in the replay; every other rule says why it is not
    # in the goal model.
    held = {(d["ticker"] or "").upper() for d in dicts
            if d["rule_type"] in REPLAYED_RULE_TYPES and (d["ticker"] or "").upper() in values
            and (d["ticker"] or "").upper() not in in_sleeve}
    unmodelled = {}
    for d in dicts:
        tk, rt = (d["ticker"] or "").upper(), d["rule_type"]
        if rt == "drawdown_guard":
            continue
        if rt not in REPLAYED_RULE_TYPES:
            unmodelled[d["id"]] = ("an entry, alert or weight rule, not a sale the goal model replays"
                                   if rt in ("strategy_signal", "buy_dip", "price_above", "price_below", "buy_back")
                                   else "an alert or a weight rule, not a sale the goal model replays")
        elif tk not in values and tk not in in_sleeve:
            unmodelled[d["id"]] = f"you don't hold {tk}"
        elif rt == "profit_protect" and not (d.get("params") or {}).get("activated"):
            unmodelled[d["id"]] = "not active yet: it starts protecting once the gain reaches its trigger"
    out = rules_spec(dicts, prices=prices, sleeve_tickers=tickers, holding_tickers=held)
    out.update({"_rule_rows": dicts, "_prices": prices, "_sleeve_tickers": tickers,
                "unmodelled": unmodelled,
                "holding_weights": {tk: values[tk] / nav for tk in held if nav},
                "holding_currency": {tk: ccys.get(tk) for tk in held}})
    return out


#: PR 123: the rule types the goal replay acts on (sales); the guard is the book's.
REPLAYED_RULE_TYPES = ("stop_loss", "trailing_stop", "profit_protect", "take_profit")


async def _holding_series(rules_ctx: dict | None, series: dict) -> dict:
    """PR 123: price history for the held tickers with rules that the solve's own
    fetch (sleeves + class proxies) did not bring."""
    from app.core.offload import offload
    want = [tk for tk in (rules_ctx or {}).get("holding_weights") or {} if tk not in series]
    if not want:
        return {}
    got, _no, _unavail = await offload(fi._fetch_blocking, want)
    return got


def _attach_holding_returns(rules_ctx: dict | None, prep: dict, series: dict) -> None:
    """PR 123: each held ticker's ILS returns on the solve's calendar; a ticker
    that cannot be priced on it is reported, not guessed."""
    if not rules_ctx or not rules_ctx.get("holding_weights"):
        return
    rets, short = {}, []
    for tk in rules_ctx["holding_weights"]:
        r = fi.ticker_ils(prep, series.get(tk), (rules_ctx.get("holding_currency") or {}).get(tk))
        if r is None:
            short.append(tk)
        else:
            rets[tk] = r
    rules_ctx["holding_returns"] = rets
    for d in rules_ctx.get("_rule_rows") or []:
        if (d["ticker"] or "").upper() in short:
            rules_ctx.setdefault("unmodelled", {})[d["id"]] = "too little price history to replay it"
    for tk in short:
        (rules_ctx.get("holdings") or {}).pop(tk, None)


async def _goal_context(session, user, rows, *, roi_pct: float | None = None,
                        target_date: str | None = None, monthly: float | None = None,
                        nominal_nav: float | None = None) -> dict | None:
    """The plan's goal as the solve needs it, from the SAME functions /plan uses.

    None when the plan has no ROI target and deadline: there is nothing to
    steer by, and the solve falls back to managing risk.

    ``nominal_nav`` (PR 136a, the starter book): an EMPTY book is scored as if it
    held this much. A "%/yr on every shekel" goal does not depend on the amount --
    the target is the amount grown at the goal's ROI -- so any positive amount gives
    the same chance; a fixed-amount goal needs the user's real starting amount.
    """
    from app.services.goal_service import annual_roi, compute_goal
    from app.services.plan_service import effective_caps, get_plan
    from app.services.standing_order_service import monthly_total
    from app.services.strategy_service import _snapshot

    plan = await get_plan(session, user)
    nav = float(_snapshot(rows).get("nav") or 0.0)
    if roi_pct is not None or target_date is not None:
        # PR 104b: the Edit-goal sheet's preview -- the same goal formula on the
        # plan as it would be saved, nothing written.
        from types import SimpleNamespace
        cols = ("target_roi_pct", "target_roi_period", "target_date", "target_amount", "objective",
                "risk_tolerance", "kelly_fraction", "return_confidence", "concentration_cap",
                "volatility_cap", "cash_target_pct", "cash_target_ils")
        plan = SimpleNamespace(**{c: getattr(plan, c, None) for c in cols})
        if roi_pct is not None:
            plan.target_roi_pct, plan.target_roi_period = float(roi_pct), "yearly"
        if target_date is not None:
            plan.target_date = str(target_date)
    nominal = False
    if nav <= 0 and nominal_nav and nominal_nav > 0:
        nav, nominal = float(nominal_nav), True
    if plan is None or getattr(plan, "target_roi_pct", None) is None or nav <= 0:
        return None
    g = await compute_goal(session, user, plan, nav)
    if nominal and not g.get("target") and g.get("years_left") and annual_roi(plan) is not None:
        # Nothing put in yet: the goal is this amount grown at the goal's ROI.
        g = {**g, "target": nav * (1.0 + float(annual_roi(plan))) ** float(g["years_left"]),
             "basis": "contributions"}
    if not g.get("target") or not g.get("years_left"):
        return None
    caps = effective_caps(plan)
    try:
        monthly_now = float(await monthly_total(session, user))
    except Exception:  # noqa: BLE001 -- a goal without deposits is still a goal
        monthly_now = 0.0
    target = float(g["target"])
    if monthly is not None:
        # PR 104b preview: a deposit raises a "ROI on every shekel" goal too --
        # the extra deposits compounded at the goal's ROI (PR 99's lever rule).
        roi_a = annual_roi(plan) if g.get("basis") == "contributions" else None
        extra = max(0.0, float(monthly) - monthly_now)
        if roi_a is not None and extra > 0:
            target += _fv_monthly(extra, roi_a, float(g["years_left"]))
    else:
        monthly = monthly_now
    return {"nav": nav, "target": target, "years": float(g["years_left"]),
            "deadline": g.get("deadline"), "monthly": monthly,
            "roi_pct": float(plan.target_roi_pct), "flows": g.get("contributions") or [],
            # PR 99: only a goal DERIVED from deposits grows with them.
            "roi_annual": annual_roi(plan) if g.get("basis") == "contributions" else None,
            "target_date_raw": getattr(plan, "target_date", None),
            "sleeve_cap_pct": float(caps.get("concentration_cap") or 0.4) * 100.0,
            "volatility_cap_pct": float(caps.get("volatility_cap") or 0.0) * 100.0 or None}


def _roi_for_amount(goal: dict, amount: float) -> float | None:
    """The yearly ROI whose goal (deposits compounded to the deadline) equals `amount`.

    Uses `goal_service.compound` itself, so "set your ROI to X%" produces
    exactly the goal amount the card quotes.
    """
    from datetime import date

    from app.services.goal_service import compound
    try:
        flows = [(date.fromisoformat(str(f["at"])[:10]), float(f["amount_ils"])) for f in goal["flows"]]
        deadline = date.fromisoformat(str(goal["deadline"])[:10])
    except (KeyError, TypeError, ValueError):
        return None
    if not flows or compound(flows, -0.99, deadline) > amount:
        return None
    lo, hi = -0.99, 5.0
    if compound(flows, hi, deadline) < amount:
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if compound(flows, mid, deadline) < amount else (lo, mid)
    return round(hi * 100, 1)


# PR 97: which firings re-solve the goal at once, and how often at most.
RESOLVE_ON = frozenset({"stop_loss", "trailing_stop", "profit_protect", "take_profit", "buy_back",
                        "trend_break", "drawdown_guard", "strategy_signal"})
RESOLVE_MIN_AGE_H = 1.0
#: PR 131: the screens' automatic re-solve of an out-of-date goal chance runs at
#: most once in this many hours (6 minutes), however many tabs ask.
AUTO_RESOLVE_MIN_AGE_H = 0.1
RESOLVE_ON_RULE_FIRE = True


async def resolve_after_rule(session, user, fired: list[dict]) -> dict | None:
    """Re-solve the goal right after a protective rule fired. At most hourly.

    The nightly solve is a schedule; a firing is news. Skipped when nothing
    that changes the book's risk fired, or when a verdict is under an hour old
    (two firings in one price run must not cost two solves).
    """
    if not RESOLVE_ON_RULE_FIRE:
        return None
    kinds = {(f or {}).get("rule_type") for f in fired or []}
    if not kinds & RESOLVE_ON:
        return None
    row = await latest(session, user)
    age = _age_hours(row) if row is not None else None
    if age is not None and age < RESOLVE_MIN_AGE_H:
        return {"skipped": "RECENT_VERDICT", "age_hours": round(age, 2)}
    out = await compute_for(session, user, source="rule_fired")
    return {"resolved": True, "outcome": out.get("outcome"), "fired": sorted(k for k in kinds if k)}


async def refresh_all(session) -> dict:
    """Nightly: one verdict per book that runs sleeves. One bad book never stops the rest."""
    from sqlalchemy import distinct, select

    from app.models.tables import PlanSleeve, User
    subjects = list((await session.scalars(select(distinct(PlanSleeve.subject)))).all())
    done = failed = 0
    for subject in subjects:
        user = (await session.scalars(select(User).where(User.email == subject))).first()
        if user is None:
            continue
        try:
            await compute_for(session, user, source="scheduled")
            done += 1
        except Exception:  # noqa: BLE001
            logger.warning("scheduled frontier solve failed for a subject", exc_info=False)
            failed += 1
    return {"frontier_solved": done, "frontier_failed": failed}


# --------------------------------------------------------------------------
# the card
# --------------------------------------------------------------------------

def _holdings_for_sale(rows) -> list[dict]:
    from app.services.recommendations import _sale_value_ils
    out = []
    for p in rows:
        q = float(p.quantity or 0)
        if q <= 0:
            continue
        gross, tax = _sale_value_ils(q, float(p.current_price or 0), float(p.cost_basis or 0),
                                     p.market, p.meta)
        out.append({"ticker": (p.ticker or "").upper(), "shares": q,
                    "value_ils": gross, "tax_ils": tax})
    return out


async def card(session, user, rows) -> list[dict]:
    """The Today card for the latest RESIZE verdict, or nothing.

    Nothing when: no verdict, a verdict past `MAX_AGE_H`, a verdict about a
    different book (a sleeve was resized since, or the objective changed), or
    an outcome other than RESIZE.
    """
    from app.services import sleeve_service as sv
    from app.services import strategy_catalog
    from app.services.plan_service import get_plan

    row = await latest(session, user)
    if row is not None and (row.detail or {}).get("mode") == "goal":
        return await _goal_cards(session, user, rows, row)
    if row is None or row.outcome != RESIZE:
        return []
    age = _age_hours(row)
    if age is None or age > MAX_AGE_H:
        return []
    v = row.detail or {}
    plan = await get_plan(session, user)
    if (getattr(plan, "objective", None) or "Balanced") != v.get("objective"):
        return []
    live = {s.strategy_id: float(s.sleeve_pct or 0.0) for s in await sv.list_sleeves(session, user)}
    for s in v.get("sleeves") or []:
        if abs(live.get(s["id"], -1.0) - float(s["current_pct"])) > 0.01:
            return []
    # Solved under different dials is a verdict about a different plan.
    st = settings_of(plan)
    if (abs(float(v.get("kelly_fraction") or v.get("kelly_target") or 0) - st["kelly_fraction"]) > 1e-6
            or (v.get("confidence") or fi.DEFAULT_CONFIDENCE) != st["return_confidence"]):
        return []
    specs = {sid: strategy_catalog.get(sid) or {} for sid in live}
    trades = plan_trades(v, specs=specs, holdings=_holdings_for_sale(rows))
    if not trades["resizes"] or not trades["sells"]:
        return []

    b, a = v["before"], v["after"]
    moves = ", ".join(f"{r['strategy_id']} {r['from_pct']:g}% -> {r['to_pct']:g}%"
                      for r in trades["resizes"])
    when = "just now" if age < 1 else f"{age:.0f}h ago"
    kelly_b = b.get("fraction_of_kelly")
    kelly_a = a.get("fraction_of_kelly")
    title = (f"Your book is {kelly_b:.2f}x full Kelly: shrink {trades['resizes'][0]['strategy_id']}"
             if kelly_b is not None else
             f"Shrink {trades['resizes'][0]['strategy_id']}: it adds risk the plan cannot use")
    sells_txt = ", ".join(f"{x['shares']:g} {x['ticker']} (~{x['value_ils']:,.0f})" for x in trades["sells"])
    buys_txt = ", ".join(f"{x['ticker']} ~{x['amount_ils']:,.0f}" for x in trades["buys"]) or "nothing (kept as cash)"
    band_txt = "; ".join(f"{s['id']}: today {s['current_pct']:g}%, band {s['band_pct'][0]:g}-{s['band_pct'][1]:g}%"
                         for s in v["sleeves"] if s.get("act"))
    return [{
        "id": "rec_frontier_book",
        "dimension": "frontier", "severity": "HIGH",
        "title": title,
        "why": (f"Your {v['objective']} plan targets {v['kelly_target']:.2f}x Kelly. As planned today the "
                f"book models at {kelly_b}x, {b['volatility_pct']:.1f}% volatility and "
                f"{b['growth_cagr_pct']:.1f}%/yr expected compound growth. Resampled {v['draws']} times "
                f"over {v['window']['sessions']} sessions in ILS; the resize is only proposed where "
                f"today's size sits above the whole stability band ({band_txt}). Return confidence: "
                f"{v.get('confidence', 'shrunk')}. Expected figures are estimates from one decade with one "
                f"bear market, not forecasts.{_target_text(v)}"),
        "action": (f"Accept resizes {moves}, sells {sells_txt}, pays ~{trades['tax_ils']:,.0f} in tax, "
                   f"and buys your core basket with the ~{trades['net_ils']:,.0f} left: {buys_txt}. "
                   f"Sleeve caps are re-levelled to the new sizes."),
        "impact": (f"Modelled on the post-trade book: {kelly_b}x -> {kelly_a}x Kelly, volatility "
                   f"{b['volatility_pct']:.1f}% -> {a['volatility_pct']:.1f}%, expected compound growth "
                   f"{b['growth_cagr_pct']:.1f}% -> {a['growth_cagr_pct']:.1f}%/yr. Core "
                   f"{v['core_pct_before']:g}% -> {v['core_pct_after']:g}% of the book."),
        "how": [f"Resize: {moves}",
                f"Sell: {sells_txt}",
                f"Tax (CGT) ~{trades['tax_ils']:,.0f}",
                f"Buy: {buys_txt}",
                *_scenario_lines(v),
                f"Your dials: {v.get('kelly_fraction', v['kelly_target']):g}x Kelly "
                f"({v.get('kelly_source', 'objective')}), confidence {v.get('confidence', 'shrunk')} "
                f"- change them on the Plan tab",
                f"Solved {when} ({v.get('version')})"],
        "apply": {"kind": CARD_KIND, "verdict_id": row.id,
                  "resizes": trades["resizes"],
                  "buy_weights": {x["ticker"]: x["weight"] for x in trades["buys"]},
                  "estimate": {"sells": trades["sells"], "buys": trades["buys"],
                               "tax_ils": trades["tax_ils"], "net_ils": trades["net_ils"]}},
        "meta": {"before": b, "after": a, "sleeves": v["sleeves"], "skipped": trades["skipped"],
                 "age_hours": round(age, 1), "kelly_target": v["kelly_target"],
                 "scenarios": v.get("scenarios"), "target": v.get("target"),
                 "confidence": v.get("confidence")},
    }]


def settings_of(plan) -> dict:
    """The plan's effective frontier dials, and where each came from."""
    objective = getattr(plan, "objective", None) or "Balanced"
    raw_k = getattr(plan, "kelly_fraction", None)
    raw_c = getattr(plan, "return_confidence", None)
    # PR 92: with a goal on the plan the goal picks the risk and Kelly is a
    # CEILING (full Kelly unless the user set a lower one); without one the
    # dial is what risk is managed to (PR 91), defaulting by objective.
    goal_mode = plan is not None and getattr(plan, "target_roi_pct", None) is not None
    if goal_mode:
        k, k_default = goal_ceiling(raw_k), fr.KELLY_MAX
        k_source = "plan" if raw_k is not None else "goal (cap 1.0x)"
    else:
        k, k_default = fr.kelly_fraction_for(objective, raw_k), fr.kelly_fraction_for(objective, None)
        k_source = "plan" if raw_k is not None else "objective"
    raw_h = getattr(plan, "halve_limit_pct", None)
    raw_t = getattr(plan, "on_track_pct", None)
    return {"mode": "goal" if goal_mode else "risk",
            # PR 104c: both limits are the plan's (migration 0027), defaults kept.
            "goal_confidence": (float(raw_t) / 100.0) if raw_t is not None else GOAL_CONFIDENCE,
            "on_track_source": "plan" if raw_t is not None else "default",
            "halve_limit_pct": float(raw_h) if raw_h is not None else HALVE_FLOOR_PCT,
            "halve_limit_source": "plan" if raw_h is not None else "default",
            "kelly_fraction": k, "kelly_default": k_default, "kelly_source": k_source,
            "kelly_range": [fr.KELLY_MIN, fr.KELLY_MAX],
            "return_confidence": fi.confidence_of(raw_c),
            "confidence_source": "plan" if raw_c else "default",
            "confidence_levels": list(fi.CONFIDENCE_LEVELS)}


# PR 104a.1: the rules a verdict's rules replay ran with. Accepting "Retune your
# protection" paused five stops and moved the guard, and the Plan tab went on
# showing the verdict solved before it -- 0.7%, "cost your goal 28 pts", the same
# card -- so Eran saw "nothing happened". A verdict is about the rules it measured.
async def rule_impacts(session, user) -> dict:
    """PR 123 (D85): the latest goal verdict's per-rule effects, for the Rules tab.

    ``fresh`` is False when the rules changed since that solve (the numbers are
    then about the old set and the screen says so).
    """
    try:
        row = await latest(session, user)
        v = (row.detail if row is not None and isinstance(row.detail, dict) else {})
    except Exception:  # noqa: BLE001 -- the Rules tab renders without impacts
        return {}
    pr = ((v.get("rules") or {}).get("per_rule") or {})
    if not pr.get("rules"):
        return {}
    try:
        fp_now = await rules_fingerprint(session, user)
    except Exception:  # noqa: BLE001
        fp_now = None
    return {"rules": pr["rules"], "computed_at": v.get("computed_at"), "base_goal_pct": pr.get("base_goal_pct"),
            "fresh": not rules_changed_since(v, fp_now)}


FP_RULE_TYPES = ("profit_protect", "trailing_stop", "stop_loss", "drawdown_guard", "take_profit")


async def rules_fingerprint(session, user) -> str:
    """The armed protective rules, as one short string: id, level, armed."""
    import hashlib

    from sqlalchemy import select

    from app.models.tables import TradingRule
    rows = (await session.scalars(select(TradingRule).where(
        TradingRule.subject == user.email, TradingRule.rule_type.in_(FP_RULE_TYPES)))).all()
    key = "|".join(sorted(f"{r.id}:{float(r.level or 0):g}:{int(bool(r.active))}" for r in rows))
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def rules_changed_since(v: dict, fp_now: str | None) -> bool:
    """True when the verdict recorded its rules and they are not today's."""
    return bool(v.get("rules_fp") and fp_now and v["rules_fp"] != fp_now)


#: Why a stored goal verdict no longer speaks for the plan, in the user's words.
#: PR 131 (D96): the screens used to fall back to the projection's rough no-rules
#: run without saying so; now they quote the last verdict and say why it is old.
STALE_TEXT = {
    "TOO_OLD": f"solved more than {MAX_AGE_H:g} hours ago",
    "RULES_CHANGED": "your protective rules changed since",
    "SLEEVES_CHANGED": "your sleeve sizes changed since",
    "SETTINGS_CHANGED": "your objective or goal settings changed since",
    "GOAL_CHANGED": "your goal changed since",
    "STALE_VIEWS": "the strategies are being re-measured",
    "NO_PLAN": "there is no plan",
}
#: Reasons whose old chance describes a different GOAL (or rests on numbers that
#: are known wrong) -- quoting it, even labelled, would answer another question.
STALE_WITHHELD = {"GOAL_CHANGED", "STALE_VIEWS", "NO_PLAN"}


def goal_stale_reason(plan, live: dict, v: dict, age: float | None,
                      fp_now: str | None = None) -> str | None:
    """Why a stored GOAL verdict is not about this plan, book and rules; None if it is."""
    if plan is None:
        return "NO_PLAN"
    g = v.get("goal") or {}
    if (g.get("roi_pct") is None
            or abs(float(g["roi_pct"]) - float(plan.target_roi_pct)) > 1e-9
            or (g.get("target_date_raw") or None) != (getattr(plan, "target_date", None) or None)):
        return "GOAL_CHANGED"
    if age is None or age > MAX_AGE_H:
        return "TOO_OLD"
    if rules_changed_since(v, fp_now):
        return "RULES_CHANGED"
    st = settings_of(plan)
    if ((getattr(plan, "objective", None) or "Balanced") != v.get("objective")
            or st["mode"] != "goal"
            or abs(float(v.get("kelly_ceiling") or 0) - st["kelly_fraction"]) > 1e-6
            or (v.get("confidence") or fi.DEFAULT_CONFIDENCE) != st["return_confidence"]
            # PR 104c: solved under the limits the plan has now.
            or abs(float(v.get("p_star") or GOAL_CONFIDENCE) - st["goal_confidence"]) > 1e-9
            or abs(float(((v.get("rules") or {}).get("tuning") or {}).get("halve_floor_pct", st["halve_limit_pct"]))
                   - st["halve_limit_pct"]) > 1e-9):
        return "SETTINGS_CHANGED"
    for s in v.get("sleeves") or []:
        if abs(live.get(s["id"], -1.0) - float(s["current_pct"])) > 0.01:
            return "SLEEVES_CHANGED"
    return None


def _goal_verdict_current(plan, live: dict, v: dict, age: float | None,
                          fp_now: str | None = None) -> bool:
    """Is a stored GOAL verdict still about this plan, this book and these rules?"""
    return goal_stale_reason(plan, live, v, age, fp_now) is None


def stale_views(v: dict) -> list[str]:
    """Sleeves whose measured view the solve could not use because it was taken
    by an older backtest engine. PR 99.1: after PR 101's a5 -> a6 bump every
    sleeve fell back to its prior until re-measured, the goal model read 0.19%/yr
    for a book it put at 17.9% the day before, and the goal cards offered a
    -7%/yr target from it. A verdict built on out-of-date measurements speaks
    for nothing; the cards and screens stand down until tonight's re-measure."""
    return [x.get("asset") for x in (v.get("views") or [])
            if not x.get("used") and x.get("reason") == fi.VIEW_STALE]


async def book_expectation(session, user) -> dict | None:
    """PR 98: the ONE expected return and volatility every screen quotes.

    When a fresh goal verdict speaks for this plan and book, its own figures for
    the book AS IT STANDS -- expected compound growth and volatility under the
    user's return confidence and today's volatility level -- are the app's
    numbers. The Plan screen's "expected return", the projection and the risk
    report then agree with the goal cards instead of quoting the measured
    backtest blend beside them. None otherwise: callers keep their measured
    figures, labelled as such.
    """
    from app.services import sleeve_service as sv
    from app.services.plan_service import get_plan
    row = await latest(session, user)
    if row is None:
        return None
    v = row.detail or {}
    if v.get("mode") != "goal" or v.get("outcome") in (NOT_MEASURABLE, UNAVAILABLE, GOAL_NO_TARGET) \
            or stale_views(v):
        return None
    live = {s.strategy_id: float(s.sleeve_pct or 0.0) for s in await sv.list_sleeves(session, user)}
    age = _age_hours(row)
    if not _goal_verdict_current(await get_plan(session, user), live, v, age,
                                 await rules_fingerprint(session, user)):
        return None
    b = v.get("before") or {}
    if b.get("growth_cagr_pct") is None or b.get("volatility_pct") is None:
        return None
    mk = v.get("market") or {}
    return {"expected_return_pct": float(b["growth_cagr_pct"]), "volatility_pct": float(b["volatility_pct"]),
            "basis": "frontier", "confidence": v.get("confidence"), "regime": mk.get("state"),
            "recent_weight": mk.get("recent_weight"), "age_hours": round(age or 0.0, 1),
            "goal_probability_pct": b.get("goal_probability_pct"), "headline": v.get("headline")}


async def goal_chance(session, user) -> dict | None:
    """PR 131 (D96): THE goal chance every screen quotes, current or not.

    `book_expectation` answers "may the verdict's return and volatility stand in
    for the book's" and must stay strict. This answers "what is the goal's
    chance", and when the verdict is merely old -- a day passed, a rule or a
    sleeve size changed -- the honest answer is the last solved chance, labelled
    with its age and why, while a re-solve runs. Until PR 131 the screens fell
    back to the projection's rough no-rules run instead, which read 44% beside
    the Plan tab's 20% for the same goal.

    The headline keys (`p_goal_pct`, `without_rules_pct`, ...) plus `current`,
    `stale_reason`, `stale_text` and `age_hours`. None when there is no goal
    verdict, or when the old chance would answer a different question
    (`STALE_WITHHELD`: the goal itself changed, or its measurements are known
    stale) -- then callers keep their own labelled fallback.
    """
    from app.services import sleeve_service as sv
    from app.services.plan_service import get_plan
    row = await latest(session, user)
    if row is None:
        return None
    v = row.detail or {}
    if v.get("mode") != "goal" or v.get("outcome") in (NOT_MEASURABLE, UNAVAILABLE, GOAL_NO_TARGET):
        return None
    hl = v.get("headline") or {}
    if hl.get("p_goal_pct") is None:
        return None
    age = _age_hours(row)
    if stale_views(v):
        reason = "STALE_VIEWS"
    else:
        live = {s.strategy_id: float(s.sleeve_pct or 0.0) for s in await sv.list_sleeves(session, user)}
        reason = goal_stale_reason(await get_plan(session, user), live, v, age,
                                   await rules_fingerprint(session, user))
    if reason in STALE_WITHHELD:
        return None
    return {**hl, "current": reason is None, "stale_reason": reason,
            "stale_text": STALE_TEXT.get(reason) if reason else None,
            "age_hours": round(age, 1) if age is not None else None}


async def goal_story_active(session, user) -> bool:
    """True when a fresh goal verdict speaks for the goal on Today.

    The older behind-your-goal card projects at the app-wide measured return;
    the goal cards use the user's own return confidence. Two goal stories on
    two bases is the contradiction this exists to prevent, so while this is
    True the older card stands down.
    """
    from app.services import sleeve_service as sv
    from app.services.plan_service import get_plan
    row = await latest(session, user)
    if row is None or (row.detail or {}).get("mode") != "goal":
        return False
    if (row.detail or {}).get("outcome") in (NOT_MEASURABLE, UNAVAILABLE, GOAL_NO_TARGET):
        return False
    live = {s.strategy_id: float(s.sleeve_pct or 0.0) for s in await sv.list_sleeves(session, user)}
    # No rules fingerprint here: a verdict made stale by a rules change still
    # OWNS the goal story -- the older behind-goal card must not come back with a
    # second, different number while the goal cards wait for the re-solve.
    return _goal_verdict_current(await get_plan(session, user), live, row.detail or {}, _age_hours(row))


def sleeve_label(sid: str) -> str:
    """What a person calls a sleeve: the tickers it holds (SOXL), not its id."""
    from app.services import strategy_catalog
    spec = strategy_catalog.get(sid) or {}
    tks = [t for t in (spec.get("weights") or {})]
    return "/".join(tks) if tks else sid


def rule_sleeve_order(rb: dict, v: dict | None = None) -> list[str]:
    """The sleeves the rules replay's per-sleeve lists (pct_time_out,
    exits_per_year, ...) are aligned to.

    Zipping `rb["sleeves"]` (an object) against those lists paired each figure
    with the wrong sleeve on Postgres, where JSONB returns object keys sorted:
    "time out of the market" read SOXL's number under QQQ. `sleeve_ids` is the
    order itself; a verdict stored before it falls back to the verdict's own
    sleeve rows, a JSON list, whose order survives."""
    if rb.get("sleeve_ids"):
        return list(rb["sleeve_ids"])
    keys = list(rb.get("sleeves") or {})
    rows = [s.get("id") for s in ((v or {}).get("sleeves") or []) if s.get("id") in keys]
    return rows + [k for k in keys if k not in rows]


def _protect_summary(rb: dict, tu: dict, v: dict | None = None) -> str:
    """The protection card's one or two sentences: what the rules cost, in the
    rules' own terms, else how the setup was chosen."""
    if rb.get("effect_pts") is not None and rb["effect_pts"] <= -_material():
        outs = [sleeve_label(sid) for sid, p in zip(rule_sleeve_order(rb, v),
                                                      (rb.get("on") or {}).get("pct_time_out") or []) if p]
        who = " and ".join(outs) if outs else "the holding"
        return (f"After a stop fires, the app keeps {who} out until the price falls back below the sale price, "
                f"so in rising markets {'they sit' if len(outs) > 1 else 'it sits'} out. That costs your goal "
                f"{abs(rb['effect_pts']):g} points.")
    return (f"{_setups_replayed(tu)}; this one has the best goal "
            f"odds with the chance of halving at or under {tu.get('halve_floor_pct', HALVE_FLOOR_PCT):g}%.")


def _setups_replayed(tu: dict) -> str:
    """PR 121: say how many setups were measured -- never "every setup"."""
    n = tu.get("tried") or len(tu.get("candidates") or [])
    return f"{n} setups of your protective rules were replayed on the same paths"


def _breach_line(tu: dict, cur_s: dict, best_s: dict) -> str:
    """D83: when today's rules break the halving limit, say so -- and what fixing it costs."""
    if not tu.get("breach"):
        return ""
    cost = round(cur_s["p_goal_pct"] - best_s["p_goal_pct"], 1)
    return (f" Your rules today leave a {cur_s['p_halve_pct']:g}% chance the book halves, above your "
            f"{tu['halve_floor_pct']:g}% limit."
            + (f" Staying inside it costs {cost:g} points of goal odds." if cost > 0 else ""))


def _take_profit_line(tu: dict, ch: dict) -> str:
    """PR 121: claim a take-profit result only when the search measured one."""
    if not tu.get("take_profits_measured"):
        return ""
    if any(d.startswith("take_profit") for d in ch.get("pause_rules") or []):
        return ""                                   # the action says it
    return " Pausing the take-profits was measured too and did not do better."


def _guard_settings_steps(gs: dict | None) -> list[str]:
    """PR 121: the guard's sell size / re-arm gap moves, in words."""
    if not gs:
        return []
    f, t = gs["from"], gs["to"]
    out = []
    if t["fraction_pct"] != f["fraction_pct"]:
        out.append(f"have the guard sell {t['fraction_pct']:g}% (was {f['fraction_pct']:g}%)")
    if t["rearm_gap_pts"] != f["rearm_gap_pts"]:
        out.append(f"re-arm the guard {t['rearm_gap_pts']:g} points under its level (was {f['rearm_gap_pts']:g})")
    return out


def _chip(label: str, before, after, fmt: str = "{:g}", unit: str = "") -> dict:
    """PR 103: one before -> after chip, both sides formatted the same way."""
    def f(x):
        return "-" if x is None else (fmt.format(x) + unit)
    return {"label": label, "before": f(before), "after": f(after)}


async def _goal_cards(session, user, rows, row) -> list[dict]:
    """PR 92: the goal's cards -- a mix card when a trade materially helps the
    goal, and lever cards when even the best mix leaves it under 70%."""
    from app.services import sleeve_service as sv
    from app.services import strategy_catalog
    from app.services.plan_service import get_plan

    v = row.detail or {}
    age = _age_hours(row)
    plan = await get_plan(session, user)
    live = {s.strategy_id: float(s.sleeve_pct or 0.0) for s in await sv.list_sleeves(session, user)}
    if v.get("outcome") in (NOT_MEASURABLE, UNAVAILABLE, GOAL_NO_TARGET) or not _goal_verdict_current(
            plan, live, v, age, await rules_fingerprint(session, user)):
        return []
    if stale_views(v):
        return []
    g, b, a, lv = v["goal"], v["before"], v["after"], v.get("levers") or {}
    star = round(v.get("p_star", GOAL_CONFIDENCE) * 100)
    conf = v.get("confidence", "shrunk")
    when = "just now" if age < 1 else f"{age:.0f}h ago"
    deadline = g.get("deadline") or "your deadline"
    goal_line = (f"Your goal: {g['target']:,.0f} by {deadline} ({g.get('roi_pct'):g}%/yr on what you put in) - "
                 f"from {g['nav']:,.0f} today that needs ~{lv.get('required_cagr_pct')}%/yr compound. "
                 f"'On track' means at least a {star}% chance. Estimate: {conf}; risk capped at "
                 f"{v['kelly_ceiling']:g}x full Kelly ({v.get('kelly_source')}).")
    # PR 93: the same goal on fat-tailed history, said on every goal card.
    tl = v.get("tail") or {}
    tb, ta = tl.get("before") or {}, tl.get("after") or {}
    # PR 99: ONE headline chance, the same figure everywhere it appears: with
    # the rules as they work today, the same replay's rules-off figure beside it.
    hl = v.get("headline") or {}
    if hl.get("p_goal_pct") is not None:
        goal_line += (f" Your chance today: {hl['p_goal_pct']:g}%"
                      + (f" with your protective rules as they work ({hl['without_rules_pct']:g}% without them)."
                         if hl.get("without_rules_pct") is not None else "."))
    if tb:
        goal_line += (f" On {tl.get('paths', 2000):,} paths built from real market days (centred on your "
                      f"estimate) the worst 5% of outcomes end near {tb['worst5_mean']:,.0f}.")
    # PR 94: how much the CORE can move the goal, said once so the levers make sense.
    # PR 97: what the market is doing now, and how much that moved the inputs.
    mk = v.get("market") or {}
    if mk.get("ok"):
        comp = mk.get("components") or {}
        state = {"risk_on": "calm (risk-on)", "neutral": "mixed (neutral)", "risk_off": "stressed (risk-off)"}.get(
            mk.get("state"), mk.get("state"))
        line = (f" Markets now: {state} - trend {'up' if comp.get('trend_up') else 'down'}"
                + (f", volatility at the {comp['vol_percentile']:g}th percentile of the last two years"
                   if comp.get("vol_percentile") is not None else "")
                + f"; recent volatility weighs {float(mk.get('recent_weight') or 0):.0%} in these numbers")
        moved = [a for a in (mk.get("vol") or []) if abs(float(a.get("scale") or 1) - 1) >= 0.10]
        if moved:
            top = max(moved, key=lambda a: abs(a["scale"] - 1))
            line += (f" ({top['id']} {top['recent_vol_pct']:g}% volatility lately vs {top['long_run_vol_pct']:g}% "
                     f"long-run)")
        goal_line += line + "."
    # PR 95: the stress case and the protective rules, said on every goal card.
    st = v.get("stress") or {}
    if st.get("before_p_pct") is not None:
        goal_line += (f" Stress case (shrunk estimate, backtests trusted least): {st['before_p_pct']:g}% chance.")
    rb = v.get("rules") or {}
    if rb.get("on"):
        on, off, fix = rb["on"], rb["off"], rb["rebound"]
        goal_line += (f" Chance the book halves at some point: {on['p_halve_pct']:g}% with your rules, "
                      f"{off['p_halve_pct']:g}% without. If a stop waited for a 15% rebound before buying back: "
                      f"{fix['p_goal_pct']:g}% chance, {fix['p_halve_pct']:g}% halving.")
        if rb.get("effect_pts") is not None and rb["effect_pts"] <= -_material():
            outs = [f"{sleeve_label(sid)} {p:g}%" for sid, p in zip(rule_sleeve_order(rb, v), on.get("pct_time_out") or []) if p]
            goal_line += (f" Your rules cost this goal {abs(rb['effect_pts']):g} points: after a stop or take-profit "
                          f"the app keeps the holding out until its price falls back below the sale price, so in "
                          f"rising markets it stays out"
                          + (f" (time out of the market: {', '.join(outs)})." if outs else "."))
    cc = v.get("core_choice") or {}
    if cc.get("current") and cc.get("candidates"):
        spread = max(r["score_pct"] for r in cc["candidates"]) - min(r["score_pct"] for r in cc["candidates"])
        goal_line += (f" Changing your core ({len(cc['candidates'])} catalog cores modelled) moves the odds by "
                      f"at most {spread:.1f} points" + (" - the sleeves decide this goal." if spread < _material()
                                                        else "."))
    mt = lv.get("money_time_note")
    if mt:
        goal_line += (f" Adding money or time doesn't help this goal: it asks {mt['roi_pct']:g}%/yr on every "
                      f"shekel, and the book is expected to make about {mt['expected_cagr_pct']:.0f}%.")
    cards = []
    from app.services import sleeve_service as _sv2
    if cc.get("outcome") == CORE_SWITCH and await _sv2.core_strategy_id(session, user) == cc["current"]["id"]:
        cur_c, best_c = cc["current"], cc["best"]
        cards.append({
            "id": "rec_goal_core_book", "dimension": "goal", "severity": "HIGH",
            "title": (f"Switch your core to {best_c['name']}: goal odds {cur_c['score_pct']:g}% -> "
                      f"{best_c['score_pct']:g}%"),
            "why": (goal_line + f" Each catalog core was priced from its own tickers' history with your sleeves "
                    f"as they are; {best_c['name']} scored best on both methods and in "
                    f"{best_c['win_share']:.0%} of {cc['draws']} redraws."),
            "action": (f"Accept makes {best_c['name']} your core and realigns the core's holdings to its basket "
                       f"({', '.join(f'{t} {w:.0%}' for t, w in best_c['weights'].items())}). Sleeves are untouched."),
            "impact": (f"Goal odds {cur_c['model_p_pct']:g}% -> {best_c['model_p_pct']:g}% (model), "
                       f"{cur_c['tail_p_pct']:g}% -> {best_c['tail_p_pct']:g}% (history); chance the book halves "
                       f"{cur_c['p_halve_pct']:g}% -> {best_c['p_halve_pct']:g}%; volatility "
                       f"{cur_c['volatility_pct']}% -> {best_c['volatility_pct']}%."),
            "how": [f"Core: {cur_c['name']} -> {best_c['name']}", "Sells and buys only inside the core",
                    f"Solved {when}"],
            "apply": {"kind": CORE_CARD_KIND, "strategy_id": best_c["id"],
                      "from_strategy_id": cur_c["id"], "objective": v.get("objective")},
            "meta": {"core_choice": {"current": cur_c, "best": best_c}, "goal": g},
            "summary": (f"Each catalog core was priced from its own tickers with your sleeves as they are; "
                        f"{best_c['name']} scored best on both methods and in {best_c['win_share']:.0%} of "
                        f"{cc['draws']} redraws."),
            "chips": [_chip("odds", cur_c["score_pct"], best_c["score_pct"], unit="%"),
                      _chip("halving", cur_c["p_halve_pct"], best_c["p_halve_pct"], unit="%"),
                      _chip("volatility", cur_c["volatility_pct"], best_c["volatility_pct"], unit="%")]})

    # PR 96: retune the protective rules for the goal, under a halving floor.
    tu = (v.get("rules") or {}).get("tuning") or {}
    if tu.get("material") and tu.get("changes"):
        ch, cur_s, best_s = tu["changes"], tu["current"]["stats"], tu["best"]["stats"]
        steps = []
        n_tp = sum(1 for d in ch["pause_rules"] if d.startswith("take_profit"))
        n_p = len(ch["pause_rules"]) - n_tp
        if n_p:
            steps.append(f"pause {n_p} stop rule{'s' if n_p > 1 else ''}")
        if n_tp:
            steps.append(f"pause {n_tp} take-profit{'s' if n_tp > 1 else ''}")
        if ch.get("guard"):
            gd = ch["guard"]
            steps.append(f"set the drawdown guard to {gd['to_pct']:g}%" if gd.get("from_pct") is None
                         else f"move the drawdown guard {gd['from_pct']:g}% -> {gd['to_pct']:g}%")
        steps += _guard_settings_steps(ch.get("guard_settings"))
        cards.append({
            "id": "rec_goal_protect_book", "dimension": "goal", "severity": "HIGH",
            "title": (f"Bring your crash risk inside your limit: halving {cur_s['p_halve_pct']:g}% -> "
                      f"{best_s['p_halve_pct']:g}%" if tu.get("breach") else
                      f"Retune your protection: goal odds {cur_s['p_goal_pct']:g}% -> "
                      f"{best_s['p_goal_pct']:g}%"),
            "why": (goal_line + _breach_line(tu, cur_s, best_s) + f" {_setups_replayed(tu)}; this is the "
                    f"one with the best goal odds that keeps the chance of halving at or under "
                    f"{tu['halve_floor_pct']:g}%." + _take_profit_line(tu, ch)),
            "action": ("Accept will " + " and ".join(steps) + ". Nothing is bought or sold; one tap re-arms them."),
            "impact": (f"Chance of reaching your goal {cur_s['p_goal_pct']:g}% -> {best_s['p_goal_pct']:g}%; chance "
                       f"the book halves {cur_s['p_halve_pct']:g}% -> {best_s['p_halve_pct']:g}%; median at the "
                       f"deadline {cur_s['median']:,.0f} -> {best_s['median']:,.0f}; worst 5% "
                       f"{cur_s['worst5_mean']:,.0f} -> {best_s['worst5_mean']:,.0f}."),
            "how": [*(f"Pause: {d}" for d in ch["pause_rules"]),
                    *([f"Drawdown guard: {ch['guard'].get('from_pct')}% -> {ch['guard']['to_pct']:g}%"]
                      if ch.get("guard") else []),
                    *(s[0].upper() + s[1:] for s in _guard_settings_steps(ch.get("guard_settings"))),
                    "Assumes every rule card is accepted the day it fires", f"Solved {when}"],
            "apply": {"kind": RULES_CARD_KIND, "pause_rule_ids": ch["pause_rule_ids"], "guard": ch.get("guard"),
                      "guard_settings": ch.get("guard_settings")},
            "meta": {"tuning": {"current": cur_s, "best": best_s, "changes": ch}, "goal": g},
            "summary": _protect_summary(rb, tu, v),
            "chips": [_chip("odds", cur_s["p_goal_pct"], best_s["p_goal_pct"], unit="%"),
                      _chip("halving", cur_s["p_halve_pct"], best_s["p_halve_pct"], unit="%"),
                      _chip("median", cur_s["median"], best_s["median"], fmt="{:,.0f}"),
                      _chip("worst 5%", cur_s["worst5_mean"], best_s["worst5_mean"], fmt="{:,.0f}")]})

    if v.get("material") and v["outcome"] in (GOAL_LESS_RISK, GOAL_REBALANCE, GOAL_IMPROVE):
        specs = {sid: strategy_catalog.get(sid) or {} for sid in live}
        trades = plan_trades(v, specs=specs, holdings=_holdings_for_sale(rows))
        if trades["resizes"]:
            moves = ", ".join(f"{r['strategy_id']} {r['from_pct']:g}% -> {r['to_pct']:g}%" for r in trades["resizes"])
            sells_txt = ", ".join(f"{x['shares']:g} {x['ticker']} (~{x['value_ils']:,.0f})" for x in trades["sells"])
            grows = [r for r in trades["resizes"] if r.get("direction") == "grow"]
            buys_txt = (", ".join(f"{x['ticker']} ~{x['amount_ils']:,.0f}" for x in trades["buys"])
                        if trades["buys"] else ("the growing sleeve, funded from what the plan then holds "
                                                "too much of" if grows else "nothing (kept as cash)"))
            title = {GOAL_LESS_RISK: "Your goal is on track: keep it with less risk",
                     GOAL_REBALANCE: f"Rebalance to reach your goal: {b['goal_probability_pct']:g}% -> "
                                     f"{a['goal_probability_pct']:g}% chance",
                     GOAL_IMPROVE: f"Raise your goal's odds: {b['goal_probability_pct']:g}% -> "
                                   f"{a['goal_probability_pct']:g}% chance"}[v["outcome"]]
            vcap = v.get("volatility_cap_pct")
            vcap_txt = (f" Volatility after is {a['volatility_pct']:.1f}%, above your plan's "
                        f"{vcap:g}% limit." if vcap and a["volatility_pct"] > vcap else "")
            cards.append({
                "id": "rec_goal_mix_book", "dimension": "goal", "severity": "HIGH",
                "title": title,
                "why": (goal_line + f" Resampled {v['draws']} times; a sleeve only moves when today's size "
                        f"sits outside its whole stability band. Estimates, not forecasts.{vcap_txt}"),
                "action": (f"Accept resizes {moves}"
                           + (f", sells {sells_txt} (~{trades['tax_ils']:,.0f} tax)" if trades["sells"] else "")
                           + f", and buys {buys_txt}. Sleeve caps are re-levelled."),
                "impact": (f"Chance of reaching your goal {b['goal_probability_pct']:g}% -> "
                           f"{a['goal_probability_pct']:g}%; median at the deadline {b['median_at_deadline']:,.0f} -> "
                           f"{a['median_at_deadline']:,.0f}; expected compound growth {b['growth_cagr_pct']}% -> "
                           f"{a['growth_cagr_pct']}%/yr; volatility {b['volatility_pct']}% -> {a['volatility_pct']}%."
                           + (f" On real-market paths: {tb['p_goal_pct']:g}% -> {ta['p_goal_pct']:g}% chance, "
                              f"worst 5% {tb['worst5_mean']:,.0f} -> {ta['worst5_mean']:,.0f}, halving "
                              f"{tb['p_halve_pct']:g}% -> {ta['p_halve_pct']:g}%." if tb and ta else "")),
                "how": [f"Resize: {moves}", *([f"Sell: {sells_txt}"] if sells_txt else []),
                        f"Buy: {buys_txt}", f"Solved {when} ({v.get('version')})"],
                "apply": {"kind": CARD_KIND, "verdict_id": row.id, "resizes": trades["resizes"],
                          "buy_weights": {x["ticker"]: x["weight"] for x in trades["buys"]},
                          "estimate": {"sells": trades["sells"], "buys": trades["buys"],
                                       "tax_ils": trades["tax_ils"], "net_ils": trades["net_ils"]}},
                "meta": {"goal": g, "before": b, "after": a, "sleeves": v["sleeves"], "levers": lv,
                         "outcome": v["outcome"], "age_hours": round(age, 1)},
                "summary": (f"Resampled {v['draws']} times; a sleeve moves only when today's size sits outside "
                            f"its whole stability band.{vcap_txt}"),
                "chips": [_chip("odds", b["goal_probability_pct"], a["goal_probability_pct"], unit="%"),
                          _chip("median", b["median_at_deadline"], a["median_at_deadline"], fmt="{:,.0f}"),
                          _chip("growth", b["growth_cagr_pct"], a["growth_cagr_pct"], unit="%/yr"),
                          _chip("volatility", b["volatility_pct"], a["volatility_pct"], unit="%")],
            })

    base = a if v.get("material") else b
    tail_base = (ta if v.get("material") else tb) or {}
    p_sized = lv.get("sized_p_goal_pct")
    if p_sized is None:                       # a verdict from before PR 99
        p_sized = min(base["goal_probability_pct"], tail_base.get("p_goal_pct", 100.0))
    sized_words = {"after_retune": " after you accept 'Retune your protection'",
                   "rules_as_they_are": " with your protective rules as they are"}.get(lv.get("sized_on"), "")
    if p_sized < star:
        alt = []
        if lv.get("extra_monthly_ils"):
            alt.append(f"add {lv['extra_monthly_ils']:,}/month")
        # PR 99.1: a target at or below zero is not a goal to offer.
        reachable_roi = lv.get("roi_at_confidence_pct") is not None and lv["roi_at_confidence_pct"] > 0
        if reachable_roi:
            alt.append(f"aim for {lv['roi_at_confidence_pct']:g}%/yr")
        if lv.get("deadline_extra_years"):
            alt.append(f"give it {lv['deadline_extra_years']:g} more years")
        others = "; ".join(alt)
        head = (f"Even the best mix within your risk cap leaves a {p_sized:g}% chance{sized_words} "
                f"(target {star}%)." if v["outcome"] in (GOAL_OUT_OF_REACH, GOAL_IMPROVE) else
                f"After the rebalance the chance is {p_sized:g}%{sized_words} (target {star}%).")
        if v["outcome"] == GOAL_TAIL_SHORT:
            head = (f"The model gives {base['goal_probability_pct']:g}%, but on real-market paths it is "
                    f"{tail_base.get('p_goal_pct')}% - not on track (target {star}%).")
        elif v["outcome"] == GOAL_TAIL_DISAGREES:
            head += (" A trade that looked better in the model lowers the odds on real-market paths, so it is "
                     "not offered.")
        sized = {"after_retune": " Sized on your book after 'Retune your protection'.",
                 "rules_as_they_are": " Sized on your protective rules as they are."}.get(lv.get("sized_on"), "")
        common_why = (goal_line + " " + head + (f" Ways to close it: {others}." if others else "")
                      + f"{sized} Solved {when}.")
        if lv.get("extra_monthly_ils"):
            new_total = round(float(g.get("monthly") or 0) + float(lv["extra_monthly_ils"]), 2)
            cards.append({
                "id": "rec_goal_deposit_book", "dimension": "goal", "severity": "HIGH",
                "title": f"Add {lv['extra_monthly_ils']:,}/month for a {star}% chance of your goal",
                "why": common_why,
                "action": (f"Accept sets your standing orders to {new_total:,.0f}/month in total. "
                           f"No money moves now; each deposit is recorded on its day."),
                "impact": (f"Your goal becomes {lv['deposit_goal_after']:,.0f} by {deadline} (each deposit also has "
                           f"to grow {g.get('roi_pct'):g}%/yr), with about a {star}% chance of reaching it."
                           if lv.get("deposit_goal_after") else
                           f"Raises the chance of reaching {g['target']:,.0f} by {deadline} to about {star}%."),
                "how": [f"Monthly deposits: {float(g.get('monthly') or 0):,.0f} -> {new_total:,.0f}",
                        "Change or pause it any time under Holdings › Set liquid cash"],
                "apply": {"kind": "set_standing_order", "monthly_total_ils": new_total,
                          "add_ils": float(lv["extra_monthly_ils"])},
                "meta": {"lever": "deposit", "goal": g, "levers": lv},
                "summary": (f"Each deposit also has to grow {g.get('roi_pct'):g}%/yr, so your goal becomes "
                            f"{lv['deposit_goal_after']:,.0f}." if lv.get("deposit_goal_after") else
                            f"Raises the chance of reaching {g['target']:,.0f} by {deadline} to about {star}%."),
                "chips": [_chip("monthly", float(g.get("monthly") or 0), new_total, fmt="{:,.0f}"),
                          _chip("goal", g["target"], lv.get("deposit_goal_after") or g["target"], fmt="{:,.0f}"),
                          _chip("odds", p_sized, star, unit="%")]})
        roi_now = float(g.get("roi_pct") or 0)
        if reachable_roi and lv["roi_at_confidence_pct"] < roi_now:
            cards.append({
                "id": "rec_goal_target_book", "dimension": "goal", "severity": "MEDIUM",
                "title": f"Set a target this book can reach: {lv['roi_at_confidence_pct']:g}%/yr ({star}% chance)",
                "why": common_why,
                "action": (f"Accept changes your ROI target from {roi_now:g}% to {lv['roi_at_confidence_pct']:g}%/yr, "
                           f"so the goal becomes about {lv['amount_at_confidence']:,.0f} by {deadline}."),
                "impact": "Your goal stops asking for growth this book is not estimated to deliver.",
                "how": ["Only the target changes; nothing is bought or sold",
                        "Raise it again any time on the Plan tab"],
                "apply": {"kind": "set_plan", "fields": {"target_roi_pct": lv["roi_at_confidence_pct"]}},
                "meta": {"lever": "target", "goal": g, "levers": lv},
                "summary": (f"At {roi_now:g}%/yr your goal asks for growth this book is not estimated to deliver: "
                            f"a {p_sized:g}% chance{sized_words}."),
                "chips": [_chip("target", roi_now, lv["roi_at_confidence_pct"], unit="%/yr"),
                          _chip("goal", g["target"], lv["amount_at_confidence"], fmt="{:,.0f}"),
                          _chip("odds", p_sized, star, unit="%")]})
        extra = lv.get("deadline_extra_years")
        if extra and extra <= 15 and g.get("deadline"):
            new_year = int(str(g["deadline"])[:4]) + math.ceil(extra)
            cards.append({
                "id": "rec_goal_deadline_book", "dimension": "goal", "severity": "MEDIUM",
                "title": f"Move your deadline to {new_year} for a {star}% chance",
                "why": common_why,
                "action": f"Accept moves your goal deadline from {str(g['deadline'])[:4]} to {new_year}.",
                "impact": (f"Your goal becomes {lv['deadline_goal_after']:,.0f} by {new_year} (another "
                           f"{math.ceil(extra)} year(s) at {g.get('roi_pct'):g}%/yr), with about a {star}% chance "
                           f"of reaching it." if lv.get("deadline_goal_after") else
                           f"Gives the book {math.ceil(extra)} more year(s) to compound toward your goal."),
                "how": ["Only the deadline changes; nothing is bought or sold"],
                "apply": {"kind": "set_plan", "fields": {"target_date": str(new_year)}},
                "meta": {"lever": "deadline", "goal": g, "levers": lv},
                "summary": (f"Another {math.ceil(extra)} year(s) at {g.get('roi_pct'):g}%/yr makes your goal "
                            f"{lv['deadline_goal_after']:,.0f}." if lv.get("deadline_goal_after") else
                            f"Gives the book {math.ceil(extra)} more year(s) to compound toward your goal."),
                "chips": [_chip("deadline", int(str(g["deadline"])[:4]), new_year, fmt="{:d}"),
                          _chip("goal", g["target"], lv.get("deadline_goal_after") or g["target"], fmt="{:,.0f}"),
                          _chip("odds", p_sized, star, unit="%")]})
    # PR 103: "Your goal" is said ONCE, above the goal group, not on every card:
    # the goal and its one headline chance up front, the rest behind More.
    head = (f"Your goal: {g['target']:,.0f} by {deadline} ({g.get('roi_pct'):g}%/yr on what you put in)."
            + (f" Your chance today: {hl['p_goal_pct']:g}%"
               + (f" with your protective rules as they work ({hl['without_rules_pct']:g}% without them)."
                  if hl.get("without_rules_pct") is not None else ".")
               if hl.get("p_goal_pct") is not None else "")
            + (f" Adding money or time doesn't help this goal: it asks {mt['roi_pct']:g}%/yr on every shekel, "
               f"and the book is expected to make about {mt['expected_cagr_pct']:.0f}%." if mt else ""))
    for c in cards:
        c["goal_summary"] = goal_line.strip()
        c["goal_summary_head"] = head
    return cards


def _target_text(v: dict) -> str:
    """The card's sentence about the user's own ROI target. Empty without one."""
    t = v.get("target")
    sc = v.get("scenarios") or {}
    if not t or not sc:
        return ""
    roi, conf = t["roi_pct"], t["confidence"]
    mine = sc.get(conf) or {}
    out = (f" Your target is {roi:g}%/yr. On the {conf} estimate this book compounds "
           f"{mine.get('before_growth_pct')}% today and {mine.get('after_growth_pct')}% after the resize; ")
    if not t["reachable"]:
        out += (f"the most any mix of these assets compounds is {mine.get('max_growth_pct')}%/yr, so the "
                f"target is not reachable on this estimate at any risk level - more risk past full Kelly "
                f"lowers compound growth.")
    elif t["after_meets"]:
        out += "the target is still met after the resize."
    else:
        out += "the resize takes the book below the target."
    if t.get("measured_drops_below"):
        m = sc.get(fi.CONFIDENCE_MEASURED) or {}
        out += (f" If the backtests are right (measured), the book compounds {m.get('before_growth_pct')}% "
                f"today - above your target - and {m.get('after_growth_pct')}% after: below it.")
    return out


def _scenario_lines(v: dict) -> list[str]:
    sc = v.get("scenarios") or {}
    return [f"{c}: {s['before_growth_pct']}% -> {s['after_growth_pct']}%/yr (max {s['max_growth_pct']}%)"
            for c, s in sc.items()]


# --------------------------------------------------------------------------
# accept
# --------------------------------------------------------------------------

async def apply_resize(session, user, spec: dict) -> dict:
    """Execute a `frontier_resize` card. Refuses UNTOUCHED when the book moved.

    Order matters and is the funded-buy order: price every buy first, then
    resize, then sell, then buy out of what the sale actually raised. Shares are
    recomputed from the holdings at the moment of Accept, so a price move since
    the card was built changes the amounts, never the proportions.
    """
    from app.providers.registry import guarded_quote
    from app.services import sleeve_service as sv
    from app.services.funding_service import MIN_TRADE_ILS
    from app.services.intake_service import credit_cash, list_positions
    from app.services.recommendations import _apply_redeploy_cash, _offload, _sell_shares
    from app.services.strategy_service import _arm_sleeve_caps

    resizes = spec.get("resizes") or []
    live = {s.strategy_id: float(s.sleeve_pct or 0.0) for s in await sv.list_sleeves(session, user)}
    for r in resizes:
        if abs(live.get(r["strategy_id"], -1.0) - float(r["from_pct"])) > 0.01:
            return {"ok": False, "refused": "BOOK_CHANGED",
                    "error": f"{r['strategy_id']} is no longer {r['from_pct']:g}% - nothing was changed. "
                             f"Refresh the frontier and look again."}
    weights = spec.get("buy_weights") or {}
    for tk in weights:
        try:
            q = await _offload(guarded_quote, tk)
        except Exception:  # noqa: BLE001
            q = None
        if q is None or not getattr(q, "price", None):
            return {"ok": False, "refused": "NO_PRICE",
                    "error": f"no live price for {tk} - nothing was sold."}

    resized = []
    for r in resizes:
        res = await sv.add_or_resize(session, user, r["strategy_id"], float(r["to_pct"]))
        if not res.get("ok"):
            await session.rollback()
            return {"ok": False, "refused": "RESIZE_REFUSED", "error": res.get("reason") or res.get("error")}
        resized.append({"strategy_id": r["strategy_id"], "from_pct": r["from_pct"], "to_pct": r["to_pct"]})
    await session.commit()

    rows = await list_positions(session, user)
    sold, tax_total, net_total = [], 0.0, 0.0
    for r in resizes:
        frac = float(r["fraction_sold"])
        for tk in r["tickers"]:
            held = [p for p in rows if (p.ticker or "").upper() == tk]
            qty = sum(float(p.quantity or 0) for p in held)
            if qty <= 0 or frac <= 0:
                continue
            res = await _sell_shares(session, user, held, qty * frac)
            net = res["gross"] - res["tax"]
            await credit_cash(session, user, net)
            tax_total += res["tax"]
            net_total += net
            sold.append({"ticker": tk, "shares": round(res["shares"], 6), "price": res.get("price"),
                         "value_ils": round(res["gross"], 2), "tax_ils": round(res["tax"], 2)})
        rows = await list_positions(session, user)

    total_w = sum(weights.values())
    legs = []
    for tk, w in weights.items():
        amt = round(net_total * w / total_w, 2) if total_w else 0.0
        if amt >= MIN_TRADE_ILS:
            legs.append({"ticker": tk, "amount_ils": amt})
    bought = await _apply_redeploy_cash(session, user, {"legs": legs}) if legs else {"bought": [], "skipped": []}
    got = bought.get("bought", [])

    # PR 92: a GROWING sleeve is funded by the one path that obeys "funding may
    # only sell what the plan has too much of" -- after the resize above, the
    # plan wants more of this sleeve and less of everything else. That path
    # writes its own ledger legs, so this handler records ITS legs itself too
    # and tells the boundary not to record them a second time.
    grown = []
    grows = [r for r in resizes if r.get("direction") == "grow"]
    if grows:
        from app.services import ledger_service as ledger
        from app.services.strategy_service import load_basket
        await ledger.record_legs(session, user, sold=sold, bought=got, source=CARD_KIND,
                                 note="frontier resize: shrink legs")
        for r in grows:
            res = await load_basket(session, user, r["strategy_id"], sleeve_pct=float(r["to_pct"]),
                                    mode="fund", honour_holds=True)
            grown.append({"strategy_id": r["strategy_id"], "ok": res.get("ok", True),
                          "sold": res.get("sold") or [], "bought": res.get("bought") or [],
                          "error": res.get("error"), "shortfall": res.get("shortfall")})
    caps = await _arm_sleeve_caps(session, user)
    await session.commit()
    note = ("Resized " + ", ".join(f"{r['strategy_id']} {r['from_pct']:g}% -> {r['to_pct']:g}%" for r in resized)
            + ". Bought " + (", ".join(f"{b['ticker']} {b['amount_ils']:,.0f}" for b in got) or "nothing")
            + f" with the {net_total:,.0f} raised"
            + ("; funded " + ", ".join(g_["strategy_id"] for g_ in grown) + " from the plan's overweights" if grown else "")
            + ". Sleeve caps re-levelled.")
    out_extra = {"grown": grown, "ledger_recorded": True} if grows else {}
    return {"note": note, "resized": resized, "sold": sold, "bought": got, **out_extra,
            "skipped_buys": bought.get("skipped", []), "tax_ils": round(tax_total, 2),
            "net_raised_ils": round(net_total, 2), "caps": caps,
            "broker_note": "Tracked book updated - no brokerage order was placed."}


async def apply_core_switch(session, user, spec: dict) -> dict:
    """PR 94: make the goal's best core the core, then realign the core to it.

    Refuses UNTOUCHED when the core is no longer the one the card was solved
    for. The realign is `_apply_core_to_basket` -- the one path that prices
    every buy before it sells and re-plans from live rows -- so switching the
    core and bringing the holdings to it cannot drift apart.
    """
    from app.services import sleeve_service as sv
    from app.services.recommendations import _apply_core_to_basket

    now = await sv.core_strategy_id(session, user)
    if now != spec.get("from_strategy_id"):
        return {"ok": False, "refused": "CORE_CHANGED",
                "error": f"your core is now {now or 'not set'}, not {spec.get('from_strategy_id')} - nothing was changed."}
    res = await sv.set_core(session, user, str(spec.get("strategy_id")))
    if not res.get("ok"):
        await session.rollback()
        return {"ok": False, "refused": "CORE_REFUSED", "error": res.get("reason") or res.get("error")}
    await session.commit()
    detail = dict(await _apply_core_to_basket(session, user, {"objective": spec.get("objective")}) or {})
    detail["core_switched"] = {"from": res.get("previous"), "to": res.get("strategy_id")}
    detail["note"] = (f"Core switched from {res.get('previous')} to {res.get('strategy_id')}. "
                      + (detail.get("note") or "Core holdings realigned to its basket."))
    return detail


async def apply_rules_tuning(session, user, spec: dict) -> dict:
    """PR 96: pause the rules the goal is better without; re-level (or arm) the guard.

    PR 121: may also pause take-profits (by rule id, like the stops) and move
    the guard's sell size and re-arm gap, which are settings on the plan.

    Refuses UNTOUCHED when the rule set moved since the solve: a rule to pause
    is gone or already paused, the guard is no longer at the level the card
    measured, or its sell size / re-arm gap is no longer what was measured.
    Pausing is reversible (one tap on Holdings › Rules); nothing is bought or sold.
    """
    import uuid

    from app.models.tables import TradingRule
    from app.services.rules_service import create_rule, update_rule

    rules = []
    for rid in spec.get("pause_rule_ids") or []:
        try:
            r = await session.get(TradingRule, uuid.UUID(str(rid)))
        except Exception:  # noqa: BLE001
            r = None
        if r is None or r.subject != user.email or not r.active:
            return {"ok": False, "refused": "RULES_CHANGED",
                    "error": "a rule this card would pause has changed since it was measured - nothing was changed."}
        rules.append(r)
    gd = spec.get("guard") or None
    guard_rule = None
    if gd and gd.get("rule_id"):
        try:
            guard_rule = await session.get(TradingRule, uuid.UUID(str(gd["rule_id"])))
        except Exception:  # noqa: BLE001
            guard_rule = None
        if (guard_rule is None or guard_rule.subject != user.email
                or abs(float(guard_rule.level) - float(gd.get("from_pct") or 0)) > 1e-6):
            return {"ok": False, "refused": "RULES_CHANGED",
                    "error": "your drawdown guard has changed since this was measured - nothing was changed."}
    gs = spec.get("guard_settings") or None
    if gs:
        from app.services.advanced_rules import derisk_fraction, rearm_gap_pts
        now = {"fraction_pct": round(derisk_fraction() * 100, 2), "rearm_gap_pts": round(rearm_gap_pts(), 2)}
        if any(abs(now[k2] - float(gs["from"][k2])) > 1e-6 for k2 in now):
            return {"ok": False, "refused": "RULES_CHANGED",
                    "error": "your drawdown guard's settings have changed since this was measured - nothing was changed."}
    paused = []
    for r in rules:
        r.active = False
        paused.append({"id": str(r.id), "ticker": r.ticker, "rule_type": r.rule_type, "level": float(r.level)})
    await session.commit()
    guard_out = None
    if gd:
        if guard_rule is not None:
            res = await update_rule(session, user, str(guard_rule.id), level=float(gd["to_pct"]), force=True)
            guard_out = {"from_pct": gd.get("from_pct"), "to_pct": res.get("level"), "ok": res.get("ok")}
        else:
            made = await create_rule(session, user, ticker="__BOOK__", rule_type="drawdown_guard", mode="pct",
                                     level=float(gd["to_pct"]), note="armed by the goal's protection card")
            await session.commit()
            guard_out = {"from_pct": None, "to_pct": float(made.level), "ok": True}
    settings_out = None
    if gs:
        from app.services import thresholds
        from app.services.plan_service import get_plan
        plan = await get_plan(session, user)
        dflt = thresholds.defaults(plan)
        merged = dict(plan.thresholds or {})
        for key, val in (("guard_sell_fraction_pct", gs["to"]["fraction_pct"]),
                         ("guard_rearm_gap_pts", gs["to"]["rearm_gap_pts"])):
            if abs(float(val) - float(dflt[key])) < 1e-9:
                merged.pop(key, None)                   # back to the default: no override
            else:
                merged[key] = float(val)
        plan.thresholds = thresholds.clean(merged) or None
        await session.commit()
        settings_out = dict(gs["to"])
    steps = [f"paused {len(paused)} rule(s)"] if paused else []
    if settings_out:
        steps.append(f"guard sells {settings_out['fraction_pct']:g}% and re-arms "
                     f"{settings_out['rearm_gap_pts']:g} points under its level")
    if guard_out:
        steps.append(f"drawdown guard {guard_out['from_pct'] if guard_out['from_pct'] is not None else 'armed'}"
                     f" -> {guard_out['to_pct']:g}%")
    return {"rules_paused": paused, "guard": guard_out, "guard_settings": settings_out,
            "note": "Protection retuned for your goal: " + "; ".join(steps)
                    + ". Paused rules stay on Holdings › Rules. Nothing was bought or sold."}
