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
    """Gather the book's shape, then solve. Reads only."""
    rows = await sv.list_sleeves(session, user)
    if not rows:
        return {"outcome": NOT_MEASURABLE, "reason": "NO_SLEEVES",
                "detail": "add a sleeve before asking what size it would have to be",
                "execution_plan": None}

    sleeves = []
    for r in rows:
        spec = strategy_catalog.get(r.strategy_id)
        if not spec:
            return {"outcome": NOT_MEASURABLE, "reason": "UNKNOWN_STRATEGY",
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

    from app.services.intake_service import list_positions
    positions = await list_positions(session, user)
    held = {}
    for p in positions:
        px = float(p.current_price or 0.0)
        if px > 0:
            tk = p.ticker.upper()
            held[tk] = held.get(tk, 0.0) + float(p.quantity) * px
    sleeve_tks = await sv.sleeve_tickers(session, user)
    core = blend.core_spec(blend.core_weights_from(held, sleeve_tks))

    bench_tk = get_settings().benchmark_ticker
    needed = set()
    for s in sleeves:
        needed |= set(bt.tickers_needed(s["spec"]))
    needed |= set(core.get("weights") or {})
    out = await offload(_solve_blocking, sorted(needed), bench_tk, sleeves, core,
                        target_excess_pct, max_drawdown_pct, floor, cap)

    # T3 -- what it costs, attached only when there IS a size to cost.
    if out.get("measured"):
        nav = sum(held.values()) + float(await _cash(session, user))
        horizon = float(getattr(plan, "horizon_years", None) or 10)
        out["cost"] = cost_of(out["measured"], nav_ils=nav, horizon_years=horizon)
        out["cost"]["funding"] = await _funding_preview(
            session, user, sleeves, out.get("solved_total_sleeve_pct"), nav)
    return out


async def _cash(session, user) -> float:
    try:
        from app.services.intake_service import get_cash
        return float(await get_cash(session, user) or 0.0)
    except Exception:  # noqa: BLE001
        logger.warning("target: cash unavailable; NAV excludes it", exc_info=False)
        return 0.0


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
