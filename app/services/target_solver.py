"""T2 - what would it take to beat the benchmark by X, without exceeding Y drawdown?

The Plan screen can offer a sleeve size but cannot say what the size buys. This
answers that, and -- more usefully -- says when nothing buys it.

**The objective, stated once, because whatever goes in the box is what gets
optimized for:**

    maximise   excess CAGR over the benchmark
    subject to blended max drawdown <= D           (hard, user-set)
               the concentration cap and cash floor hold
    report     excess at equal risk, alongside

The drawdown ceiling is not a figure reported afterwards -- it constrains the
ADMISSIBLE SET. Without it the answer is always "more leverage": anyone beats SPY
by holding 1.3x SPY, and every sleeve in this family is a leveraged or
concentrated instrument, so raw excess is maximised by the most leveraged blend
that the caps happen to allow. That is not a strategy, it is a slider at 100%.

**Read-only, without exception.** Nothing here writes to ``plan_sleeves``,
positions or cash. ``execution_plan`` is always ``None``; ``would_execute``
describes the diff a human would apply through the existing Resize sleeve and
Fund all sleeves controls. A return target one tap from a book change is the C5
slider bug with higher stakes.
"""
from __future__ import annotations

import logging
import math

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.offload import offload
from app.engines import blend
from app.engines import strategy_backtest as bt
from app.models.tables import User
from app.services import sleeve_service as sv
from app.services import strategy_catalog
from app.services.backtest_service import _fetch
from app.services.plan_service import effective_caps, get_plan
from app.services.funding_service import cash_floor_pct

logger = logging.getLogger(__name__)

SOLVER_VERSION = "s1"

# The five answers. Every one of them is a legitimate result -- including the
# three that are a "no". A slider that cannot say no implies every position on
# it is attainable.
REACHED = "REACHED"
REACHED_ABOVE_CAP = "REACHED_ABOVE_CAP"
DRAWDOWN_BOUND = "DRAWDOWN_BOUND"
UNREACHABLE = "UNREACHABLE"
NOT_MEASURABLE = "NOT_MEASURABLE"

# Solved on a 1-point grid, DISPLAYED on the 5-point grid the slider uses.
# Solving on the display grid would make the answer an artefact of the widget.
COARSE_STEP_PCT = 5.0
FINE_STEP_PCT = 1.0
DISPLAY_STEP_PCT = 5.0


def _split(sleeves: list[dict]) -> list[float]:
    """How a total sleeve share is divided between the sleeves already run.

    T2 sweeps ONE axis -- total sleeve share, split at the current ratio -- so
    the answer is "how much bigger, all of them together", not an optimum over
    each sleeve independently. That search is T6. The card must say so: a
    one-axis answer presented as an optimum is a lie of omission.
    """
    total = sum(float(s.get("current_pct") or 0.0) for s in sleeves)
    if total <= 0:
        return [1.0 / len(sleeves)] * len(sleeves)
    return [float(s.get("current_pct") or 0.0) / total for s in sleeves]


def _components(sleeves, ratios, core, total_pct, core_pct):
    out = [{"id": s["id"], "spec": s["spec"], "weight": total_pct * r / 100.0}
           for s, r in zip(sleeves, ratios)]
    if core and core.get("weights"):
        out.append({"id": "__core__", "spec": core, "weight": core_pct / 100.0})
    return out


def solve(series: dict, *, sleeves: list[dict], core: dict | None,
          benchmark: list | None, target_excess_pct: float,
          max_drawdown_pct: float, cash_floor: float = 0.0,
          concentration_cap: float = 1.0) -> dict:
    """Pure. Given price history and the book's shape, return one verdict.

    ``sleeves`` is ``[{"id", "spec", "current_pct"}]``; ``core`` is a
    ``blend.core_spec`` or None. ``cash_floor`` and ``concentration_cap`` are
    FRACTIONS (0..1), matching ``funding_service.cash_floor_pct`` and
    ``plan_service.effective_caps``.
    """
    if not sleeves:
        return {"outcome": NOT_MEASURABLE, "reason": "NO_SLEEVES",
                "detail": "this book runs no sleeves, so there is nothing to size"}

    ratios = _split(sleeves)
    max_total = max(0.0, 100.0 - cash_floor * 100.0)
    cap_pct = concentration_cap * 100.0
    # The cap is judged against the positions THIS DECISION creates -- the
    # tickers the sleeves can hold. Judging it book-wide lets an
    # already-concentrated core veto every sleeve size including zero, which
    # says nothing about the choice being made and hides the sleeve that is
    # actually breaching. The core's own concentration is reported separately,
    # because it is real, but it is not this decision's constraint.
    sleeve_tickers = set()
    for s_ in sleeves:
        sleeve_tickers |= blend.claimable_tickers(s_["spec"])
    evaluated: list[dict] = []
    seen: dict[float, dict] = {}

    def at(total_pct: float) -> dict | None:
        total_pct = round(min(max(total_pct, 0.0), max_total), 4)
        if total_pct in seen:
            return seen[total_pct]
        core_pct = max(0.0, max_total - total_pct)
        comps = _components(sleeves, ratios, core, total_pct, core_pct)
        if not comps:
            return None
        m = blend.measure_blend(series, comps, benchmark=benchmark, detail=False)
        if not m.get("ok"):
            seen[total_pct] = {"total_pct": total_pct, "ok": False, "m": m}
            return seen[total_pct]
        by_tk = m.get("peak_weight_pct_by_ticker") or {}
        sleeve_peaks = {tk: w for tk, w in by_tk.items() if tk in sleeve_tickers}
        peak_tk = max(sleeve_peaks, key=sleeve_peaks.get) if sleeve_peaks else None
        peak = sleeve_peaks.get(peak_tk, 0.0)
        point = {
            "total_pct": total_pct, "ok": True,
            "excess_pct": m.get("excess_cagr_pct"),
            "cagr_pct": m.get("cagr_pct"),
            "max_drawdown_pct": m.get("max_drawdown_pct"),
            "peak_ticker": peak_tk,
            "peak_ticker_weight_pct": peak,
            "core_peak_ticker": m.get("peak_ticker"),
            "core_peak_weight_pct": m.get("peak_ticker_weight_pct") or 0.0,
            "cap_ok": peak <= cap_pct + 1e-9,
            "dd_ok": m.get("max_drawdown_pct") <= max_drawdown_pct + 1e-9,
            "m": m,
        }
        seen[total_pct] = point
        evaluated.append(point)
        return point

    # Coarse sweep first, refined only where the answer actually turns. A 1-point
    # sweep of the whole range is ~97 simulations of ten years of daily closes
    # for a number that changes smoothly; the coarse-then-fine pass gets the same
    # answer in about a quarter of the work.
    grid = [i * COARSE_STEP_PCT for i in range(int(max_total // COARSE_STEP_PCT) + 1)]
    if max_total not in grid:
        grid.append(max_total)
    for g in grid:
        p = at(g)
        if p is None or not p["ok"]:
            m = (p or {}).get("m") or {}
            return {"outcome": NOT_MEASURABLE,
                    "reason": m.get("reason") or "BLEND_FAILED",
                    "detail": m.get("detail") or "the blend could not be measured",
                    "component": m.get("component")}

    def hits(p) -> bool:
        return p["excess_pct"] is not None and p["excess_pct"] >= target_excess_pct

    def refine(pred, lo: float, hi: float) -> dict | None:
        """Smallest total share in (lo, hi] satisfying pred, on the fine grid."""
        best = None
        x = lo
        while x <= hi + 1e-9:
            p = at(x)
            if p and p["ok"] and pred(p):
                best = p
                break
            x = round(x + FINE_STEP_PCT, 4)
        return best

    def smallest(pred) -> dict | None:
        coarse = [p for p in sorted(evaluated, key=lambda q: q["total_pct"])
                  if pred(p)]
        if not coarse:
            return None
        first = coarse[0]
        lo = max(0.0, first["total_pct"] - COARSE_STEP_PCT + FINE_STEP_PCT)
        return refine(pred, lo, first["total_pct"]) or first

    admissible = lambda p: p["cap_ok"] and p["dd_ok"]              # noqa: E731

    # 1. Reached, inside every constraint.
    win = smallest(lambda p: admissible(p) and hits(p))
    if win:
        return _verdict(REACHED, win, sleeves, ratios, target_excess_pct,
                        max_drawdown_pct, series, core, benchmark, cash_floor,
                        evaluated)

    # 2. Reached, but only past the concentration cap.
    over_cap = smallest(lambda p: p["dd_ok"] and not p["cap_ok"] and hits(p))
    if over_cap:
        v = _verdict(REACHED_ABOVE_CAP, over_cap, sleeves, ratios,
                     target_excess_pct, max_drawdown_pct, series, core,
                     benchmark, cash_floor, evaluated)
        v["binding_constraint"] = {
            "kind": "concentration_cap",
            "cap_pct": round(cap_pct, 2),
            "ticker": over_cap["peak_ticker"],
            "would_reach_pct": round(over_cap["peak_ticker_weight_pct"], 2),
        }
        return v

    # 3. Reached on return, but only by breaching the drawdown ceiling. The most
    #    informative outcome in the design: it separates "your strategies are too
    #    weak" from "your risk tolerance is what binds".
    over_dd = smallest(lambda p: p["cap_ok"] and not p["dd_ok"] and hits(p))
    if over_dd:
        best_ok = _best(evaluated, admissible)
        v = _verdict(DRAWDOWN_BOUND, over_dd, sleeves, ratios, target_excess_pct,
                     max_drawdown_pct, series, core, benchmark, cash_floor,
                     evaluated)
        v["binding_constraint"] = {
            "kind": "drawdown_ceiling",
            "ceiling_pct": round(max_drawdown_pct, 2),
            "would_require_pct": round(over_dd["max_drawdown_pct"], 2),
        }
        v["best_within_ceiling"] = ({
            "total_sleeve_pct": best_ok["total_pct"],
            "excess_pct": best_ok["excess_pct"],
            "max_drawdown_pct": best_ok["max_drawdown_pct"],
        } if best_ok else None)
        v["floor"] = _floor_note(evaluated, max_drawdown_pct)
        return v

    # 4. Nothing admissible reaches it at any size.
    best_ok = _best(evaluated, admissible)
    v = _verdict(UNREACHABLE, best_ok, sleeves, ratios, target_excess_pct,
                 max_drawdown_pct, series, core, benchmark, cash_floor, evaluated)
    v["binding_constraint"] = _weakest_component(v.get("measured") or {}, sleeves)
    v["floor"] = _floor_note(evaluated, max_drawdown_pct)
    return v


def _floor_note(evaluated, max_drawdown_pct) -> dict | None:
    """Is the ceiling below what the book does with NO sleeve at all?

    Without this, "nothing is admissible" reads as "your sleeves are too risky"
    when the real answer can be that the core alone already exceeds the ceiling.
    Those need opposite responses -- shrink the sleeve, versus raise the ceiling
    or change the core -- so the difference has to be on the card.
    """
    ok = [p for p in evaluated if p["ok"]]
    if not ok:
        return None
    base = min(ok, key=lambda p: p["total_pct"])
    breaches = base["max_drawdown_pct"] > max_drawdown_pct + 1e-9
    return {
        "at_zero_sleeve_pct": base["total_pct"],
        "max_drawdown_pct": base["max_drawdown_pct"],
        "breaches_ceiling": breaches,
        "note": ("your book already exceeds this ceiling with no sleeve at all "
                 "— the ceiling, or the core, is what has to move"
                 if breaches else None),
    }


def _best(evaluated, pred):
    ok = [p for p in evaluated if p["ok"] and pred(p) and p["excess_pct"] is not None]
    return max(ok, key=lambda p: p["excess_pct"]) if ok else None


def _weakest_component(measured: dict, sleeves) -> dict | None:
    """Which sleeve's own measured excess is holding the blend down.

    "You cannot get there" is a dead end. "You cannot get there because this
    sleeve measured +1.2% against a benchmark doing +9%" is a work item -- it
    points at the rule to fix rather than at the slider to drag.
    """
    comps = measured.get("components") or []
    bench = measured.get("benchmark_cagr_pct")
    ids = {s["id"] for s in sleeves}
    rows = [c for c in comps if c["id"] in ids and c.get("cagr_pct") is not None]
    if not rows or bench is None:
        return None
    worst = min(rows, key=lambda c: c["cagr_pct"])
    return {
        "kind": "component_excess",
        "component": worst["id"],
        "component_cagr_pct": worst["cagr_pct"],
        "benchmark_cagr_pct": bench,
        "component_excess_pct": round(worst["cagr_pct"] - bench, 2),
    }


def _verdict(outcome, point, sleeves, ratios, target_excess_pct,
             max_drawdown_pct, series, core, benchmark, cash_floor,
             evaluated) -> dict:
    """Assemble the payload, re-measuring the chosen point IN FULL.

    The sweep runs with ``detail=False`` because the per-component and cash-drag
    simulations do not change the blended figures and would quadruple the work.
    The point actually reported is measured again with everything on, so the card
    never renders a figure the sweep did not compute -- a card may not claim what
    it did not produce, and "the sweep skipped it" is not a measurement.
    """
    out = {
        "outcome": outcome,
        "solver_version": SOLVER_VERSION,
        "target": {"excess_pct": target_excess_pct,
                   "max_drawdown_pct": max_drawdown_pct},
        "axis": "total_sleeve_share",
        "axis_note": ("Sleeves are sized together, at their current ratio. "
                      "Sizing each independently is a wider search, not this one."),
        "curve": [{"total_sleeve_pct": p["total_pct"],
                   "excess_pct": p["excess_pct"],
                   "max_drawdown_pct": p["max_drawdown_pct"],
                   "admissible": p["cap_ok"] and p["dd_ok"]}
                  for p in sorted(evaluated, key=lambda q: q["total_pct"])],
        "execution_plan": None,       # Phase T is read-only, without exception
    }
    if point is None:
        out["measured"] = None
        out["would_execute"] = None
        return out

    core_pct = max(0.0, 100.0 - cash_floor * 100.0 - point["total_pct"])
    comps = _components(sleeves, ratios, core, point["total_pct"], core_pct)
    full = blend.measure_blend(series, comps, benchmark=benchmark, detail=True)
    out["measured"] = full if full.get("ok") else None
    out["solved_total_sleeve_pct"] = point["total_pct"]
    # Rounded UP to the slider's grid: rounding down would land under the target
    # the user asked for, which is the one direction that must not happen.
    steps = int(point["total_pct"] / DISPLAY_STEP_PCT)
    if point["total_pct"] % DISPLAY_STEP_PCT:
        steps += 1
    out["display_total_sleeve_pct"] = min(100.0, DISPLAY_STEP_PCT * steps)
    out["would_execute"] = _would_execute(sleeves, ratios, point["total_pct"])
    return out


def _would_execute(sleeves, ratios, total_pct) -> dict:
    """The diff, described. Nothing in Phase T consumes this.

    ``resizes`` is what ``sleeve_service.add_or_resize`` takes. ``legs`` is left
    NULL on purpose: a leg is a share count at a price, and a price carries a
    ``price_as_of`` that has to be checked before anything is staged. Building
    priced legs in a read-only phase would manufacture exactly the stale-quote
    claim investing-discipline 1b exists to prevent. Phase A fills them from
    ``plan_funding`` at apply time, in the shape documented here.
    """
    return {
        "resizes": [{"strategy_id": s["id"],
                     "from_pct": round(float(s.get("current_pct") or 0.0), 2),
                     "to_pct": round(total_pct * r, 2)}
                    for s, r in zip(sleeves, ratios)],
        "legs": None,
        "legs_reason": "priced at apply time; see Phase A",
        "legs_schema": ["side", "ticker", "market", "quantity", "order_type",
                        "limit_price", "estimated_price_ils", "estimated_cgt_ils",
                        "price_as_of"],
    }


# --------------------------------------------------------------------------
# T3 - what the target costs
# --------------------------------------------------------------------------

def recovery_pct(drawdown_pct: float) -> float:
    """The gain needed to get back to level after a fall of this size.

    Asymmetric, and the asymmetry is the whole point: down 45% needs +82%, not
    +45%. A drawdown reported on its own invites the reader to net it against
    the return above it, which is arithmetic that does not work.
    """
    d = max(0.0, min(float(drawdown_pct), 99.999)) / 100.0
    return round((d / (1.0 - d)) * 100.0, 2) if d else 0.0


def geometric_to_arithmetic_pct(cagr_pct: float, volatility_pct: float) -> float:
    """Convert a MEASURED CAGR into the drift SimulationEngine expects.

    The engine draws ``exp((mu - sigma^2/2)T + sigma sqrt(T) z)``, so ``mu`` is
    the arithmetic (log) drift and the MEDIAN outcome lands at
    ``exp((mu - sigma^2/2)T)``. A measured CAGR is a geometric return -- it is
    what the path actually compounded at -- so it belongs on the median, not on
    the mean. Feeding it in as ``mu`` directly would shift the whole
    distribution down by sigma^2/2 and understate every percentile.

    Converting here means the median projects at the rate that was measured, and
    the mean sits above it by exactly the volatility drag -- which is the honest
    version of "a target expressed as an average is not the outcome you are most
    likely to get".
    """
    sigma = max(0.0, float(volatility_pct)) / 100.0
    g = float(cagr_pct) / 100.0
    if g <= -1.0:
        return float(cagr_pct)
    # Deliberately NOT rounded: this feeds the simulation, it is not displayed,
    # and rounding a drift throws away precision the projection then compounds.
    return (math.log1p(g) + 0.5 * sigma ** 2) * 100.0


def cost_of(measured: dict, *, nav_ils: float, horizon_years: float = 10.0,
            seed: int = 7) -> dict:
    """What the measured blend costs to hold: drawdown, distribution, tax.

    Pure apart from the Monte Carlo, which is seeded, so two calls with the same
    inputs give the same answer -- a projection that moves between refreshes is
    not something anyone can act on.
    """
    from app.engines.simulation_engine import SimulationEngine

    dd = float(measured.get("max_drawdown_pct") or 0.0)
    nav = max(0.0, float(nav_ils or 0.0))
    out = {
        "drawdown": {
            "pct": round(dd, 2),
            "ils": round(nav * dd / 100.0, 2),
            "recovery_pct": recovery_pct(dd),
            "note": (f"a {dd:.1f}% fall needs +{recovery_pct(dd):.1f}% to get "
                     f"back to level"),
        },
        "tax": {
            "gross_cagr_pct": measured.get("gross_cagr_pct"),
            "net_cagr_pct": measured.get("cagr_pct"),
            "drag_pct_per_year": measured.get("tax_drag_pct"),
            "cgt_rate_pct": measured.get("cgt_rate_pct"),
            "note": "the cost to STAY in it; the cost to arrive is the funding CGT",
        },
        "projection": None,
    }
    if nav <= 0:
        return out

    vol = float(measured.get("volatility_pct") or 0.0)
    mu = geometric_to_arithmetic_pct(float(measured.get("cagr_pct") or 0.0), vol)
    sim = SimulationEngine(seed=seed).run(
        initial_value=nav, expected_return_pct=mu, volatility_pct=vol,
        horizon_years=horizon_years)
    # REAL terms lead, per investing-discipline 2, with the assumption stated.
    # Median beside mean on both, because for a leveraged blend the median sits
    # well below the mean and that gap IS the finding.
    out["projection"] = {
        "horizon_years": horizon_years,
        "basis": "real",
        "real": {"median_ils": round(sim.real.p50, 2),
                 "mean_ils": round(sim.real.mean, 2),
                 "p5_ils": round(sim.real.p5, 2),
                 "p95_ils": round(sim.real.p95, 2)},
        "nominal": {"median_ils": round(sim.nominal.p50, 2),
                    "mean_ils": round(sim.nominal.mean, 2)},
        "probability_of_real_loss": round(sim.probability_of_loss_real, 3),
        "median_below_mean_pct": (
            round((1.0 - sim.real.p50 / sim.real.mean) * 100.0, 2)
            if sim.real.mean else None),
        "runs": sim.runs,
        "assumptions": list(sim.assumptions) + [
            "measured CAGR anchors the MEDIAN, not the mean",
            "in today's purchasing power (real), CPI-deflated",
        ],
    }
    return out


# --------------------------------------------------------------------------
# the session-bound half
# --------------------------------------------------------------------------

async def solve_for(session: AsyncSession, user: User, *,
                    target_excess_pct: float, max_drawdown_pct: float) -> dict:
    """Gather the book's shape, then solve. Reads only.

    The gathering lives in `_book_for_solve` and is SHARED with the T6a split
    search -- not duplicated. When it was duplicated, the copy had three wrong
    signatures and 500'd in production.
    """
    book, err = await _book_for_solve(session, user)
    if err:
        return err
    sleeves, core = book["sleeves"], book["core"]
    cap, floor, nav = book["cap"], book["floor"], book["nav"]
    bench_tk, needed, unpriced = book["bench_tk"], book["needed"], book["unpriced"]
    plan = book["plan"]
    out = await offload(_solve_blocking, sorted(needed), bench_tk, sleeves, core,
                        target_excess_pct, max_drawdown_pct, floor, cap)

    if unpriced:
        # A holding with no price is not a holding worth zero. Report it: it
        # changes NAV, which changes every shekel figure on the card.
        out["degraded"] = sorted(set((out.get("degraded") or []) + ["price"]))
        out["unpriced_holdings"] = sorted(set(unpriced))

    # T3 -- what it costs, attached only when there IS a size to cost.
    if out.get("measured"):
        horizon = float(getattr(plan, "horizon_years", None) or 10)
        out["cost"] = cost_of(out["measured"], nav_ils=nav, horizon_years=horizon)
        out["cost"]["funding"] = await _funding_preview(
            session, user, sleeves, out.get("solved_total_sleeve_pct"), nav)

    # T5 -- attached LAST, so it can see everything the solve produced, including
    # `cost` and `degraded`. Derived from the verdict, never recomputed: if the
    # recommendation and the figures above it could disagree, the card would be
    # arguing with itself.
    out["recommendation"] = recommend(out, benchmark=out.get("benchmark") or bench_tk)
    return out


async def _funding_preview(session, user, sleeves, solved_pct, nav) -> dict | None:
    """What it would cost to ARRIVE at the solved size, via the shared planner.

    Calls the same ``plan_funding`` the sleeve path calls, with the same
    ``exclude``, rather than reimplementing the trim ranking -- a second
    implementation of one fact is the shape the card-claims batch removed.

    Read-only: ``plan_funding`` computes, it does not write.
    """
    if not solved_pct or nav <= 0:
        return None
    current = sum(float(s.get("current_pct") or 0.0) for s in sleeves)
    delta_pct = float(solved_pct) - current
    if delta_pct <= 0:
        return {"needed_ils": 0.0, "note": "the solved size is not larger than "
                                           "what you already run"}
    amount = round(nav * delta_pct / 100.0, 2)
    try:
        from app.services.funding_service import plan_funding
        from app.services.intake_service import get_cash, list_positions
        from app.services.strategy_service import _held_ils, _snapshot
        rows = await list_positions(session, user)
        snap = _snapshot(rows)
        plan = await get_plan(session, user)
        _held_ils(rows, snap)
        fund = plan_funding(
            rows, snap, plan, getattr(plan, "objective", None) or "Grow",
            float(effective_caps(plan)["concentration_cap"]), amount,
            cash_ils=float(await get_cash(session, user) or 0.0),
            exclude=await sv.sleeve_tickers(session, user))
    except Exception:  # noqa: BLE001
        logger.warning("target: funding preview unavailable", exc_info=False)
        return {"needed_ils": amount, "degraded": True,
                "note": "could not price the trims; the size stands, the cost does not"}
    return {
        "needed_ils": amount,
        "raised_ils": fund.get("funded_ils"),
        "estimated_cgt_ils": fund.get("tax_ils") or fund.get("estimated_cgt_ils"),
        "legs": fund.get("legs") or fund.get("sells"),
        "shortfall_ils": round(
            max(0.0, amount - float(fund.get("funded_ils") or 0.0)), 2),
        "note": "net proceeds, sleeves excluded as a funding source",
    }


def _solve_blocking(tickers, bench_tk, sleeves, core, target_excess_pct,
                    max_drawdown_pct, floor, cap) -> dict:
    series, missing = _fetch(sorted(set(tickers) | {bench_tk}))
    if missing:
        return {"outcome": NOT_MEASURABLE, "reason": bt.MISSING_TICKER,
                "detail": f"no price history for {', '.join(missing)}",
                "execution_plan": None}
    bench = (series.get(bench_tk) if bench_tk in tickers
             else series.pop(bench_tk, None))
    out = solve(series, sleeves=sleeves, core=core, benchmark=bench,
                target_excess_pct=target_excess_pct,
                max_drawdown_pct=max_drawdown_pct,
                cash_floor=floor, concentration_cap=cap)
    out["benchmark"] = bench_tk
    return out


# --------------------------------------------------------------------------
# T5 -- the recommendation
# --------------------------------------------------------------------------
# A card that ends in a diagnosis and no instruction leaves the reader to do the
# inference, which is the part they came here to avoid. Five outcomes were the
# right way to model the ANSWER; they are not an answer to "so what do I do".
#
# This is a pure function over the verdict on purpose. It is the sentence the
# user acts on, so it has to be testable without a database, a price feed or a
# browser -- and every number in it has to be one the solve already measured.
# Nothing here computes a new figure; it only chooses which measured figure is
# the one that matters and what to do about it.
#
# `actions` are declarative, and Phase T stays read-only: `set_ceiling` and
# `set_target` refill the card's own inputs and re-solve, `set_sleeves` is the
# handoff Phase A will wire to the tracked book. Nothing here writes anything.

_HEADROOM_PCT = 0.0   # the recommended ceiling is the measured fall, rounded up
                      # to the next whole percent -- not padded. A ceiling with
                      # invented headroom is the card choosing risk for you.


def _g(x, dflt: str = "?") -> str:
    """Format a measured number, or say so when it is absent.

    recommend() must be TOTAL. It runs at the end of every solve, and an
    instruction is decoration on top of a measurement -- if a missing figure can
    raise here, a solve that measured fine returns a 500 and the user loses the
    card entirely. That trade is never worth it, so every interpolation goes
    through this.
    """
    if x is None:
        return dflt
    try:
        return f"{float(x):g}"
    except (TypeError, ValueError):
        return dflt


def _signed(x) -> str:
    """Same as _g, but keeps the sign that makes an excess readable."""
    if x is None:
        return "?"
    try:
        return f"{float(x):+g}"
    except (TypeError, ValueError):
        return "?"


def _ceil_pct(x) -> float | None:
    """The measured fall, rounded UP to the next whole percent.

    Rounding UP matters: a recommended ceiling below what the book already does
    comes straight back as DRAWDOWN_BOUND, so the one-tap would be a button that
    changes nothing. Returns None rather than raising when there is no figure --
    the caller then offers no action instead of a broken one.
    """
    if x is None:
        return None
    try:
        return float(math.ceil(float(x) + _HEADROOM_PCT))
    except (TypeError, ValueError):
        return None


def _equal_risk_warning(m: dict, benchmark: str) -> str | None:
    """The finding that outranks the solve, when it is present.

    Excess at equal risk is leverage-proof: both sides scaled to the same
    drawdown. If it is negative, the book is not being paid for the risk it
    already carries, and NO sleeve size fixes that -- it is a statement about
    the core. That has to be said whichever of the five outcomes came back,
    because it is the more expensive problem in every one of them.
    """
    er = m.get("excess_at_equal_risk_pct")
    if er is None or er >= 0:
        return None
    return (f"At equal risk to {benchmark} your book is {abs(er):.2f}%/yr BEHIND. "
            f"That is measured with both sides scaled to the same drawdown, so it "
            f"is not a risk-taking difference -- it is the core underperforming. "
            f"No sleeve size repairs it.")


def recommend(v: dict, *, benchmark: str = "the benchmark") -> dict:
    """One imperative sentence, the measurement behind it, and the taps.

    Returns {headline, because, actions[], severity, equal_risk_warning}.
    Never invents a figure: every number comes from the verdict it was given.
    """
    outcome = v.get("outcome")
    m = v.get("measured") or {}
    tgt = v.get("target") or {}
    t_excess = tgt.get("excess_pct")
    ceiling = tgt.get("max_drawdown_pct")
    warn = _equal_risk_warning(m, benchmark)

    def out(headline, because, actions, severity):
        # An action whose value could not be computed is dropped, not rendered
        # disabled. A greyed-out button still reads as "there is a lever here",
        # and there isn't one -- offering nothing is the more honest empty state.
        # `set_sleeves` is exempt: it carries no numeric lever in Phase T, it is
        # the Phase A handoff, and it is inert by design.
        actions = [a for a in actions
                   if a.get("value") is not None or a["kind"] in ("set_sleeves", "set_target")]
        return {"headline": headline, "because": because, "actions": actions,
                "severity": severity, "equal_risk_warning": warn,
                "solver_version": SOLVER_VERSION}

    # ---------------------------------------------------------------- nothing
    if outcome == NOT_MEASURABLE:
        reason = v.get("reason") or "the solve could not run"
        detail = {
            "NO_SLEEVES": "You have no sleeves, so there is nothing to size. "
                          "Add a strategy to a sleeve first.",
            "UNKNOWN_STRATEGY": "A sleeve points at a strategy that is not in "
                                "the catalog. Fix the sleeve, then re-solve.",
        }.get(reason, "Re-run once prices are available.")
        return out("There is nothing to solve yet.", f"{reason}: {detail}", [], "warn")

    # ------------------------------------------------------------- it reaches
    if outcome == REACHED:
        size = v.get("display_total_sleeve_pct")
        dd = m.get("max_drawdown_pct")
        return out(
            f"Set your sleeves to {_g(size)}% of the book, together.",
            (f"That is the SMALLEST allocation that reaches +{_g(t_excess)}%/yr over "
             f"{benchmark}, and it did so at a {_g(dd)}% worst fall against your "
             f"{_g(ceiling)}% ceiling. Anything larger buys return you did not ask for "
             f"at risk you did not agree to."),
            [{"kind": "set_sleeves", "label": f"Size sleeves to {_g(size)}%",
              "value": size,
              "detail": "Phase A applies this to the tracked book. It never places an order."}],
            "ok")

    # ---------------------------------------------- reaches, but past the cap
    if outcome == REACHED_ABOVE_CAP:
        bc = v.get("binding_constraint") or {}
        cap = bc.get("cap_pct")
        tk = bc.get("ticker")
        need = bc.get("would_reach_pct")
        return out(
            f"Raise your concentration cap above {_g(need)}%, or lower the target.",
            (f"The target is reachable and inside your drawdown ceiling, but it puts "
             f"{_g(need)}% of the book into {tk} alone, against a {_g(cap)}% cap. The cap is "
             f"the only thing refusing this -- it is a rule you set, not a measurement."),
            [{"kind": "set_target", "label": "Lower the target instead",
              "value": None,
              "detail": "Re-solve at a lower excess to find the largest target the cap allows."}],
            "warn")

    # ------------------------------------------ the ceiling is what refuses it
    if outcome == DRAWDOWN_BOUND:
        bc = v.get("binding_constraint") or {}
        need = bc.get("would_require_pct")
        floor = v.get("floor") or {}
        best = v.get("best_within_ceiling")

        # THE case that reads as a dead end today: the core alone already exceeds
        # the ceiling, so the sleeves are not the constraint and resizing them
        # cannot help. Sending the reader back to the slider here is the single
        # most misleading thing this card could do.
        if floor.get("breaches_ceiling"):
            base_dd = floor.get("max_drawdown_pct")
            rec_ceiling = _ceil_pct(base_dd)
            return out(
                f"Your sleeves are not the problem. Raise the ceiling to at least "
                f"{_g(rec_ceiling)}%, or change the core.",
                (f"With NO sleeve at all your book still falls {_g(base_dd)}%, against the "
                 f"{_g(ceiling)}% ceiling you set. That is the core, which is most of the "
                 f"book -- so no sleeve size, including zero, can bring the blend under "
                 f"this ceiling. Until the ceiling admits what the core already does, "
                 f"the solver has nothing it is allowed to show you."),
                [{"kind": "set_ceiling",
                  "label": f"Raise the ceiling to {_g(rec_ceiling)}% and re-solve",
                  "value": rec_ceiling,
                  "detail": (f"{_g(rec_ceiling)}% is what your core already costs you. This is a "
                             f"permission, not a return -- it does not add a single percent, "
                             f"it stops the solver refusing to show you the options above "
                             f"{_g(ceiling)}%.")}],
                "bad")

        # The ordinary case: the sleeves CAN get there, but only past the ceiling.
        actions = [{"kind": "set_ceiling",
                    "label": f"Raise the ceiling to {_g(_ceil_pct(need))}% and re-solve",
                    "value": _ceil_pct(need),
                    "detail": "A permission, not a return. The risk is real and measured."}]
        alt = ""
        if best and best.get("excess_pct") is not None:
            actions.append({"kind": "set_target",
                            "label": f"Or accept +{_g(best['excess_pct'])}%/yr instead",
                            "value": best["excess_pct"],
                            "detail": (f"The best your sleeves reach at {_g(best['total_sleeve_pct'])}% "
                                       f"without breaching {_g(ceiling)}%.")})
            alt = (f" Inside the ceiling you set, the most your sleeves reach is "
                   f"+{_g(best['excess_pct'])}%/yr at {_g(best['total_sleeve_pct'])}% sleeves.")
        return out(
            f"Choose: raise the ceiling to {_g(_ceil_pct(need))}%, or take a smaller target.",
            (f"+{_g(t_excess)}%/yr is reachable on return, but only at a {_g(need)}% worst fall "
             f"against your {_g(ceiling)}% ceiling.{alt} This is the useful outcome: your "
             f"strategies are not too weak, your risk tolerance is what binds."),
            actions, "warn")

    # ------------------------------------------------- nothing reaches it, ever
    if outcome == UNREACHABLE:
        bc = v.get("binding_constraint") or {}
        best = m.get("excess_cagr_pct")
        size = v.get("display_total_sleeve_pct")
        actions = []
        if best is not None:
            actions.append({"kind": "set_target",
                            "label": f"Lower the target to +{_g(best)}%/yr",
                            "value": best,
                            "detail": f"The most any admissible size reached, at {_g(size)}% sleeves."})
        why = (f"No sleeve size between 0% and 100% reaches +{_g(t_excess)}%/yr inside your "
               f"{_g(ceiling)}% ceiling.")
        if bc.get("kind") == "component_excess":
            why += (f" The sleeve holding it down is {bc['component']}, which measured "
                    f"{_g(bc['component_cagr_pct'])}%/yr against {benchmark} at "
                    f"{_g(bc['benchmark_cagr_pct'])}%/yr -- {_signed(bc.get('component_excess_pct'))}%/yr. "
                    f"That is a rule to fix or replace, not a slider to drag.")
        headline = (f"Lower the target to +{_g(best)}%/yr, or add a strategy that can carry it."
                    if best is not None
                    else "Nothing in your catalog reaches this. The catalog is what has to change.")
        return out(headline, why, actions, "bad")

    return out("No recommendation.", f"Unrecognised outcome: {outcome}", [], "warn")


# --------------------------------------------------------------------------
# T6a -- size each sleeve on its own axis
# --------------------------------------------------------------------------
# T2 sweeps the TOTAL at the ratio the book already runs. This sweeps the
# simplex: every sleeve on its own axis. The plan's constraints, applied here
# rather than paraphrased:
#
#   - rank on the out-of-sample split `backtest_service` ALREADY computes,
#     never on the full sample the winner was chosen from
#   - show the fit/test gap on every ranked row
#   - print how many blends were searched next to the winner
#
# COST. Two simulations per point (in-sample, out-of-sample) instead of one, on
# a ~55-point grid for two sleeves. That is why this is an explicit button and
# not something the card runs on load.

def slice_series(series: dict, *, before: str | None = None,
                 since: str | None = None) -> dict:
    """Rows before / on-or-after a date. Plain data in, plain data out.

    `series` is `{ticker: [(YYYY-MM-DD, price), ...]}`, so the split is a string
    comparison -- the same shape `backtest_service` already slices on, and the
    reason `OOS_SPLIT` can be a bare date string in the first place.
    """
    out = {}
    for tk, rows in (series or {}).items():
        kept = [r for r in rows
                if (before is None or str(r[0]) < before)
                and (since is None or str(r[0]) >= since)]
        if len(kept) >= 3:
            out[tk] = kept
    return out


def solve_split(series: dict, *, sleeves: list[dict], core: dict | None,
                benchmark: list | None, max_drawdown_pct: float,
                cash_floor: float = 0.0, concentration_cap: float = 1.0,
                oos_split: str | None = None) -> dict:
    """The simplex search. Pure: price data in, verdict out."""
    from app.services import split_solver as sp

    if not sleeves:
        return {"ok": False, "reason": "NO_SLEEVES",
                "detail": "this book runs no sleeves, so there is nothing to split"}

    split_date = oos_split
    if split_date is None:
        from app.services.backtest_service import OOS_SPLIT
        split_date = OOS_SPLIT

    max_total = max(0.0, 100.0 - cash_floor * 100.0)
    cap_pct = concentration_cap * 100.0
    sleeve_tickers = set()
    for s_ in sleeves:
        sleeve_tickers |= blend.claimable_tickers(s_["spec"])

    ins = slice_series(series, before=split_date)
    oos = slice_series(series, since=split_date)
    b_ins = [r for r in (benchmark or []) if str(r[0]) < split_date] or None
    b_oos = [r for r in (benchmark or []) if str(r[0]) >= split_date] or None
    if not ins or not oos:
        return {"ok": False, "reason": "NO_OOS_WINDOW",
                "detail": (f"the history does not span {split_date}, so there is no "
                           f"out-of-sample window to rank on")}

    def components_for(point):
        core_pct = max(0.0, max_total - sum(point))
        comps = [{"id": s["id"], "spec": s["spec"], "weight": w / 100.0}
                 for s, w in zip(sleeves, point)]
        if core and core.get("weights"):
            comps.append({"id": "__core__", "spec": core, "weight": core_pct / 100.0})
        return comps

    def one(feed, bench, comps):
        m = blend.measure_blend(feed, comps, benchmark=bench, detail=False)
        return m if m.get("ok") else None

    def measure(point):
        comps = components_for(point)
        if not comps:
            return None
        a = one(ins, b_ins, comps)
        b = one(oos, b_oos, comps)
        if a is None or b is None:
            return None
        by_tk = b.get("peak_weight_pct_by_ticker") or {}
        peaks = [w for tk, w in by_tk.items() if tk in sleeve_tickers]
        peak = max(peaks) if peaks else 0.0
        # Admissibility during the SWEEP uses the worse of the two windows. That
        # is a LOWER BOUND on the true full-window drawdown -- a fall that spans
        # the split boundary is larger than either half sees. Declared in the
        # payload, and the winner is re-measured in full below before it is
        # reported, exactly as T2's _verdict re-measures its chosen point.
        dd = max(float(a.get("max_drawdown_pct") or 0.0),
                 float(b.get("max_drawdown_pct") or 0.0))
        return {"excess_pct": a.get("excess_cagr_pct"),
                "oos_excess_pct": b.get("excess_cagr_pct"),
                "max_drawdown_pct": round(dd, 2),
                "admissible": (dd <= max_drawdown_pct + 1e-9
                               and peak <= cap_pct + 1e-9)}

    found = sp.search(measure, n=len(sleeves), max_total=max_total)
    if not found.get("ok"):
        return found

    # Re-measure the winner over the WHOLE window, with detail on. The sweep's
    # drawdown is a lower bound; if the full window breaches the ceiling the
    # point is not admissible however well it ranked, and reporting it would be
    # the card claiming a figure the sweep never computed.
    best_point = tuple(found["best"]["split_pct"])
    full = blend.measure_blend(series, components_for(best_point),
                               benchmark=benchmark, detail=True)
    verified = bool(full.get("ok")) and \
        float(full.get("max_drawdown_pct") or 0.0) <= max_drawdown_pct + 1e-9
    found["full_window"] = full if full.get("ok") else None
    found["verified_in_full"] = verified
    found["sweep_note"] = (
        "the sweep judged the ceiling on the worse of the two half-windows, which "
        "is a LOWER bound -- a fall spanning the split is larger than either half "
        "sees. The winner is re-measured over the whole window before it is shown.")
    if not verified:
        found["warning"] = (
            f"the best-ranked split measures "
            f"{full.get('max_drawdown_pct')}% over the whole window, past your "
            f"{max_drawdown_pct:g}% ceiling. It ranked well on the halves and is "
            f"not admissible on the full history.")
    found["oos_split"] = split_date
    found["would_execute"] = _would_execute_split(sleeves, best_point)
    found["execution_plan"] = None          # applying is Phase A, not this
    return found


def _would_execute_split(sleeves, point) -> dict:
    """The diff, in the shape `target_apply.plan_apply` already takes.

    Same schema as `_would_execute` so the Accept button needs no second code
    path -- and `from_pct` is carried so the staleness check keeps working.
    """
    return {
        "resizes": [{"strategy_id": s["id"],
                     "from_pct": round(float(s.get("current_pct") or 0.0), 2),
                     "to_pct": round(float(w), 2)}
                    for s, w in zip(sleeves, point)],
        "legs": None,
        "legs_reason": "priced at apply time; see Phase A",
    }


async def _book_for_solve(session: AsyncSession, user: User):
    """The book's shape. ONE loader, used by both solvers.

    THIS FUNCTION WAS WRONG ON FIRST WRITE, and the way it was wrong is the
    lesson. Its previous docstring claimed it had been "factored out rather than
    copied" -- and it had in fact been RE-WRITTEN FROM MEMORY while the working
    original sat forty lines above it in the same file. Three signatures were
    wrong, none of them caught by a test:

        strategy_catalog.spec_for(...)      does not exist -- it is .get()
        is_cash_position                    lives in intake_service, not
                                            strategy_service
        cash_floor_pct(session, user)       is not async and takes
                                            (objective, plan)

    The endpoint 500'd in 0.1s, before a single simulation. `investigate-issue`
    names this exactly: "code I wrote minutes ago is the least-checked code in
    the session. Freshness feels like knowledge."

    It is now the ONLY loader -- `solve_for` calls it too, so a fourth
    divergence is not expressible rather than merely discouraged.

    Returns (book, err). `book` carries everything either solver needs.
    """
    rows = await sv.list_sleeves(session, user)
    if not rows:
        return None, {"outcome": NOT_MEASURABLE, "reason": "NO_SLEEVES",
                      "detail": "add a sleeve before asking what size it would have to be",
                      "execution_plan": None}

    sleeves = []
    for r in rows:
        spec = strategy_catalog.get(r.strategy_id)
        if not spec:
            return None, {"outcome": NOT_MEASURABLE, "reason": "UNKNOWN_STRATEGY",
                          "detail": f"{r.strategy_id} is not in the catalog",
                          "execution_plan": None}
        sleeves.append({"id": r.strategy_id,
                        "spec": {k: spec[k] for k in
                                 ("id", "weights", "base", "risk_off", "overlay")
                                 if k in spec},
                        "current_pct": float(r.sleeve_pct or 0.0)})

    plan = await get_plan(session, user)
    cap = float(effective_caps(plan)["concentration_cap"])
    floor = float(cash_floor_pct(getattr(plan, "objective", None), plan))

    from app.services.intake_service import is_cash_position, list_positions
    from app.services.strategy_service import _snapshot
    positions = await list_positions(session, user)

    # NAV comes from the app's OWN snapshot, not from a sum computed here. Two
    # implementations of NAV is two numbers that can disagree on one screen.
    nav = float(_snapshot(positions).get("nav") or 0.0)

    # CASH is a real position row (ticker CASH, market TASE) with a price of 1.
    # It must NOT enter the core basket: a backtest of the core would fetch ten
    # years of history for CASH and abstain, and cash is already modelled.
    # Excluded by the app's own predicate rather than by a ticker string, so a
    # rename cannot quietly reintroduce it.
    held, unpriced = {}, []
    for pos in positions:
        if is_cash_position(pos.ticker, pos.meta):
            continue
        px = float(pos.current_price or 0.0)
        tk = pos.ticker.upper()
        if px <= 0:
            unpriced.append(tk)     # surfaced by the caller, never dropped
            continue
        held[tk] = held.get(tk, 0.0) + float(pos.quantity) * px
    sleeve_tks = await sv.sleeve_tickers(session, user)
    core = blend.core_spec(blend.core_weights_from(held, sleeve_tks))

    bench_tk = get_settings().benchmark_ticker
    needed = set()
    for s_ in sleeves:
        needed |= set(bt.tickers_needed(s_["spec"]))
    needed |= set(core.get("weights") or {})
    return {"sleeves": sleeves, "plan": plan, "cap": cap, "floor": floor,
            "core": core, "bench_tk": bench_tk, "needed": needed,
            "nav": nav, "unpriced": unpriced}, None


async def solve_split_for(session: AsyncSession, user: User, *,
                          max_drawdown_pct: float) -> dict:
    """The T6a search for the acting user's book. Read-only."""
    book, err = await _book_for_solve(session, user)
    if err:
        return {"ok": False, **err}
    out = await offload(_split_blocking, sorted(book["needed"]), book["bench_tk"],
                        book["sleeves"], book["core"], max_drawdown_pct,
                        book["floor"], book["cap"])
    out["benchmark"] = book["bench_tk"]
    if book["unpriced"]:
        out["degraded"] = sorted(set((out.get("degraded") or []) + ["price"]))
        out["unpriced_holdings"] = sorted(set(book["unpriced"]))
    return out


def _split_blocking(tickers, bench_tk, sleeves, core, max_drawdown_pct, floor, cap):
    series, missing = _fetch(sorted(set(tickers) | {bench_tk}))
    if missing:
        return {"ok": False, "reason": bt.MISSING_TICKER,
                "detail": f"no price history for {', '.join(missing)}"}
    bench = (series.get(bench_tk) if bench_tk in tickers
             else series.pop(bench_tk, None))
    return solve_split(series, sleeves=sleeves, core=core, benchmark=bench,
                       max_drawdown_pct=max_drawdown_pct,
                       cash_floor=floor, concentration_cap=cap)
