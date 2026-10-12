"""PR 93 - does the goal survive real markets? Filtered historical paths.

PR 92 decides with a lognormal: smooth, symmetric, no crash days. The book it
decides for holds an 88%-volatility leveraged sleeve, whose real risk lives in
exactly what a lognormal leaves out -- volatility that clusters, crashes that
arrive in runs, and the compounding drag of a 3x fund through a drawdown.

So every number a goal card states is checked a second way: thousands of
paths to the user's deadline, each stitched from REAL historical days in
21-day blocks (so a crash and its aftershocks stay together), on the book's
own daily returns.

**THE DRIFT IS THE USER'S, THE SHAPE IS HISTORY'S.** Bootstrapping raw history
would silently replace the user's return confidence with the backtest's mean --
a 'shrunk' user would be shown 'measured' odds through the back door. Each
asset's daily returns are re-centred on the drift his chosen estimate assigns
it ("filtered historical simulation"): history supplies the volatility, the
tails, the correlations and the path; his estimate supplies the centre.

**DEPOSITS ARE LINEAR, SO THE LEVER IS EXACT.** Final wealth on a path is
nav * C_T + c * S, where C_T is the path's growth and S the summed growth of
a deposit made at each month-end. The deposit that gives p* of paths the goal
is therefore a percentile of (target - nav*C_T) / S -- no search.

Pure numpy, no I/O.
"""
from __future__ import annotations

import numpy as np

TRADING_DAYS = 252
MONTH_DAYS = 21
N_PATHS = 2000
BLOCK_DAYS = 21
SEED = 11


def recentre(returns: np.ndarray, drift_annual: np.ndarray, *, periods: int = TRADING_DAYS,
             vol_scale=None) -> np.ndarray:
    """Shift each column so its mean daily return is drift/periods.

    With ``vol_scale`` (PR 97) each column's deviations are also multiplied by
    its scale: history's shape at TODAY's volatility level.
    """
    r = np.asarray(returns, dtype=float)
    dev = r - r.mean(axis=0)
    if vol_scale is not None:
        s = np.ones(r.shape[1])
        vs = np.asarray(vol_scale, dtype=float)[: s.size]
        s[: vs.size] = vs
        dev = dev * s[None, :]
    return dev + np.asarray(drift_annual, dtype=float) / periods


def simulate(port_daily: np.ndarray, *, years: float, n_paths: int = N_PATHS,
             block: int = BLOCK_DAYS, seed: int = SEED) -> dict:
    """Stationary-block paths of one portfolio's daily returns to `years`.

    Returns per path: `growth` (C_T), `deposit_factor` (S: the final value of 1
    deposited at every month-end) and `worst_drawdown` (the deepest fall from a
    running peak, as a positive fraction).
    """
    r = np.asarray(port_daily, dtype=float)
    t_hist = r.size
    steps = max(1, int(round(years * TRADING_DAYS)))
    if t_hist < block * 2:
        raise ValueError("not enough history to bootstrap")
    rng = np.random.default_rng(seed)
    n_blocks = -(-steps // block)
    starts = rng.integers(0, t_hist - block + 1, size=(n_paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_paths, -1)[:, :steps]
    log_c = np.cumsum(np.log1p(np.maximum(r[idx], -0.999999)), axis=1)
    growth_t = np.exp(log_c[:, -1])
    months = np.arange(MONTH_DAYS, steps + 1, MONTH_DAYS) - 1
    if months.size:
        deposit_factor = np.exp(log_c[:, -1:] - log_c[:, months]).sum(axis=1)
    else:
        deposit_factor = np.zeros(n_paths)
    peak = np.maximum.accumulate(np.concatenate([np.zeros((n_paths, 1)), log_c], axis=1), axis=1)[:, 1:]
    worst = 1.0 - np.exp((log_c - peak).min(axis=1))
    return {"growth": growth_t, "deposit_factor": deposit_factor, "worst_drawdown": worst,
            "n_paths": n_paths, "steps": steps}


def goal_stats(sim: dict, *, nav: float, target: float, monthly: float = 0.0,
               p_star: float = 0.70) -> dict:
    """What the paths say about the goal: odds, median, the bad tail, the lever."""
    g, s = sim["growth"], sim["deposit_factor"]
    final = nav * g + monthly * s
    worst5 = np.sort(final)[: max(1, int(0.05 * final.size))]
    need = (target - nav * g - monthly * s) / np.where(s > 0, s, np.inf)
    extra = float(np.percentile(need, p_star * 100))
    return {"p_goal_pct": round(float(np.mean(final >= target)) * 100, 1),
            "median": round(float(np.median(final)), 0),
            "worst5_mean": round(float(worst5.mean()), 0),
            "p_halve_pct": round(float(np.mean(sim["worst_drawdown"] >= 0.5)) * 100, 1),
            "extra_monthly_needed": max(0.0, extra) if np.isfinite(extra) else None,
            "amount_at_confidence": round(float(np.percentile(final, (1 - p_star) * 100)), 0)}


# --------------------------------------------------------------------------
# PR 95: the same paths, with the user's protective rules firing
# --------------------------------------------------------------------------
#
# Eran, 2026-10-05: "I have other logics and rules that should protect me from
# drops." The paths above hold the book to the deadline whatever happens. Here
# his ARMED rules act on the same paths, with the semantics the app actually
# executes (read from the code, not assumed):
#
#   trailing exit      the tightest of trailing_stop / profit_protect / stop_loss
#                      on a sleeve ticker: price <= peak x (1 - trail) -> the
#                      sleeve's holding goes to cash.
#   take-profit        price >= rung -> sell `tp_fraction` (25%); next rung 10%
#                      higher (`TAKE_PROFIT_LADDER_STEP`).
#   buy-back hold      after either sale the ticker is held out until the price
#                      is BELOW the sale price (`buyback_hold`), then topped back
#                      up to its target. For a stop exit that means re-entering
#                      on the first dip below the exit -- "first_dip".
#                      `reentry="rebound"` is the alternative measured beside
#                      it: re-enter only once the price is 15% off its post-exit
#                      low.
#   drawdown guard     book >= `level` below its peak -> sell 25% of the most
#                      volatile sleeve, held out until the drawdown is back under
#                      max(level/2, level - gap), gap 2.5 points by default
#                      (`advanced_rules.guard_rearm_pct`; PR 121: a setting).
#
# Assumptions, stated: every rule card is accepted the day it fires; sleeve
# rules act on the sleeve's own return stream (the rule's ticker is what the
# sleeve holds while its trend is on); between events the book drifts and is
# topped back to target monthly, as the funder cards would; deposits land
# monthly. Rules on core tickers are not simulated -- the core is one basket.

REBOUND_PCT = 0.15
TP_STEP = 0.10


def simulate_rules(asset_daily: np.ndarray, weights: np.ndarray, *, years: float,
                   sleeves: list[dict], guard: dict | None = None, monthly: float = 0.0,
                   nav: float = 1.0, reentry: str = "first_dip", rules_on: bool = True,
                   n_paths: int = 1000, block: int = BLOCK_DAYS, seed: int = SEED,
                   rf_daily: float = 0.0) -> dict:
    """Paths of the book with its rules acting. Pure.

    ``asset_daily`` is T x K daily returns of the book's units (sleeves first,
    then the core, then cash); ``weights`` their targets (sum 1). ``sleeves`` is
    one dict per sleeve column: {"col", "trail", "tp_ratio", "tp_fraction"}
    (``trail`` a fraction, ``tp_ratio`` the take-profit price over today's).
    ``guard`` is {"level", "rearm", "fraction", "col"} or None. With
    ``rules_on=False`` the identical paths run with no rule acting.
    """
    r_all = np.asarray(asset_daily, dtype=float)
    w = np.asarray(weights, dtype=float)
    t_hist, k = r_all.shape
    steps = max(1, int(round(years * TRADING_DAYS)))
    rng = np.random.default_rng(seed)
    n_blocks = -(-steps // block)
    starts = rng.integers(0, t_hist - block + 1, size=(n_paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_paths, -1)[:, :steps]

    hold = np.outer(np.full(n_paths, nav), w)          # value per unit
    cash = np.zeros(n_paths)
    m = len(sleeves)
    cols = [s["col"] for s in sleeves]
    price = np.ones((n_paths, m))
    peak = np.ones((n_paths, m))
    out = np.zeros((n_paths, m), dtype=bool)            # held out after a sale
    out_level = np.zeros((n_paths, m))
    low = np.ones((n_paths, m))
    rung = np.array([s.get("tp_ratio") or np.inf for s in sleeves])[None, :].repeat(n_paths, 0)
    tp_held = np.zeros((n_paths, m), dtype=bool)
    tp_level = np.zeros((n_paths, m))
    guard_on = np.zeros(n_paths, dtype=bool)
    book_peak = np.full(n_paths, nav, dtype=float)
    worst = np.zeros(n_paths)
    exits = np.zeros((n_paths, m))
    days_out = np.zeros((n_paths, m))
    tps = np.zeros((n_paths, m))
    guards = np.zeros(n_paths)

    def top_up(mask_paths):
        """Move cash back into every unit not held out, toward its target."""
        total = hold.sum(axis=1) + cash
        blocked = np.zeros((n_paths, k), dtype=bool)
        for j, c in enumerate(cols):
            blocked[:, c] = out[:, j] | tp_held[:, j]
        if guard and rules_on:
            blocked[:, guard["col"]] |= guard_on
        want = np.where(blocked, hold, w[None, :] * total[:, None])
        want = np.where(mask_paths[:, None], want, hold)
        need = np.clip(want - hold, 0.0, None)
        # Buys are paid from uninvested cash only; nothing is sold to top up.
        scale = np.minimum(1.0, np.clip(cash, 0.0, None) / np.maximum(need.sum(axis=1), 1e-12))
        buy = need * scale[:, None]
        return buy

    for t in range(steps):
        r = r_all[idx[:, t]]
        hold *= (1.0 + r)
        cash *= (1.0 + rf_daily)
        if m:
            price *= (1.0 + r[:, cols])
            peak = np.where(out, peak, np.maximum(peak, price))
            low = np.where(out, np.minimum(low, price), price)
        if rules_on:
            for j, s in enumerate(sleeves):
                c = s["col"]
                trail = s.get("trail")
                if trail:
                    hit = ~out[:, j] & (price[:, j] <= peak[:, j] * (1.0 - trail))
                    if hit.any():
                        cash[hit] += hold[hit, c]
                        hold[hit, c] = 0.0
                        out[hit, j] = True
                        out_level[hit, j] = price[hit, j]
                        low[hit, j] = price[hit, j]
                        exits[hit, j] += 1
                tp = s.get("tp_ratio")
                if tp:
                    hit = ~out[:, j] & (price[:, j] >= rung[:, j])
                    if hit.any():
                        sold = hold[hit, c] * float(s.get("tp_fraction") or 0.25)
                        hold[hit, c] -= sold
                        cash[hit] += sold
                        tp_held[hit, j] = True
                        tp_level[hit, j] = price[hit, j]
                        rung[hit, j] = price[hit, j] * (1.0 + TP_STEP)
                        tps[hit, j] += 1
                # releases
                if reentry == "rebound":
                    rel = out[:, j] & (price[:, j] >= low[:, j] * (1.0 + REBOUND_PCT))
                else:
                    rel = out[:, j] & (price[:, j] < out_level[:, j])
                rel_tp = tp_held[:, j] & (price[:, j] < tp_level[:, j])
                if rel.any():
                    out[rel, j] = False
                    peak[rel, j] = price[rel, j]
                if rel_tp.any():
                    tp_held[rel_tp, j] = False
                if rel.any() or rel_tp.any():
                    mask = rel | rel_tp
                    buy = top_up(mask)
                    hold += buy
                    cash -= buy.sum(axis=1)
                days_out[:, j] += out[:, j]
            if guard:
                total = hold.sum(axis=1) + cash
                book_peak = np.maximum(book_peak, total)
                dd = 1.0 - total / book_peak
                fire = ~guard_on & (dd >= guard["level"])
                if fire.any():
                    c = guard["col"]
                    sold = hold[fire, c] * guard["fraction"]
                    hold[fire, c] -= sold
                    cash[fire] += sold
                    guard_on[fire] = True
                    guards[fire] += 1
                rearm = guard_on & (dd < guard["rearm"])
                if rearm.any():
                    guard_on[rearm] = False
                    buy = top_up(rearm)
                    hold += buy
                    cash -= buy.sum(axis=1)
        if (t + 1) % MONTH_DAYS == 0:
            # Monthly: deposits land, and every unit NOT held out is rebalanced
            # to its target both ways -- the app's bands, caps and funder cards
            # keep the book near its plan. Held-out units keep their (reduced)
            # holding; their shortfall stays in cash, as `buyback_hold` does.
            cash += monthly
            total = hold.sum(axis=1) + cash
            blocked = np.zeros((n_paths, k), dtype=bool)
            if rules_on:
                for j, c in enumerate(cols):
                    blocked[:, c] = out[:, j] | tp_held[:, j]
                if guard:
                    blocked[:, guard["col"]] |= guard_on
            hold = np.where(blocked, hold, w[None, :] * total[:, None])
            cash = total - hold.sum(axis=1)
            neg = cash < 0                          # targets above what is free: scale the free units down
            if neg.any():
                free_val = np.where(blocked[neg], 0.0, hold[neg]).sum(axis=1)
                scale = np.clip((free_val + cash[neg]) / np.maximum(free_val, 1e-12), 0.0, 1.0)
                hold[neg] = np.where(blocked[neg], hold[neg], hold[neg] * scale[:, None])
                cash[neg] = 0.0
        total = hold.sum(axis=1) + cash
        book_peak = np.maximum(book_peak, total)
        worst = np.maximum(worst, 1.0 - total / book_peak)

    final = hold.sum(axis=1) + cash
    yrs = steps / TRADING_DAYS
    return {"final": final, "worst_drawdown": worst, "n_paths": n_paths, "steps": steps,
            "exits_per_year": (exits.mean(axis=0) / yrs).round(2).tolist(),
            "take_profits_per_year": (tps.mean(axis=0) / yrs).round(2).tolist(),
            "pct_time_out": (days_out.mean(axis=0) / steps * 100).round(1).tolist(),
            "guard_fires_per_year": round(float(guards.mean() / yrs), 2)}


def rules_stats(sim: dict, *, target: float, p_star: float = 0.70) -> dict:
    final = sim["final"]
    worst5 = np.sort(final)[: max(1, int(0.05 * final.size))]
    return {"p_goal_pct": round(float(np.mean(final >= target)) * 100, 1),
            # PR 99: the largest goal these paths reach with p_star, rules acting.
            "amount_at_confidence": round(float(np.percentile(final, (1 - p_star) * 100)), 0),
            "median": round(float(np.median(final)), 0),
            "worst5_mean": round(float(worst5.mean()), 0),
            "p_halve_pct": round(float(np.mean(sim["worst_drawdown"] >= 0.5)) * 100, 1),
            "exits_per_year": sim["exits_per_year"], "take_profits_per_year": sim["take_profits_per_year"],
            "pct_time_out": sim["pct_time_out"], "guard_fires_per_year": sim["guard_fires_per_year"]}
