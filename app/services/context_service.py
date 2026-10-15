"""Assemble a compact, deterministic snapshot of the user's finances to ground
the AI assistant and the digest.

Everything here comes from the real engines, so the LLM cannot invent a number.
**A number it cannot invent can still be wrong**, and the 2026-09-01 push is how:

    "Your account is currently valued at 21,474.35 ILS, with a total return
     of 17.76%."

Both figures were real and neither was about the other. 21,474.35 was the NAV
snapshot. 17.76% was `performance.total_return_pct` -- a BACKFILL that prices
the holdings held TODAY back through 252 sessions of real closes, answering
"what would this book have done" and not "what did my money do". The account's
actual return on the 20,000 contributed was +3.89%. The model was handed two
true numbers on incompatible bases, in a flat blob with nothing saying so, and
welded them into one false sentence. The prompt said "never invent numbers" and
it did not invent one.

So every figure that can be quoted now travels as `{value, means}`: the number
and, in plain language, WHAT IT IS A MEASUREMENT OF. `digest_service.verify`
then refuses any number in the generated text that is not in here.
"""
from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import User
from app.services.fx import holding_value_ils
from app.services.allocation_mix import classify_holding
from app.services.intake_service import list_positions
from app.services.performance_service import performance
from app.services.plan_service import get_plan
from app.services.portfolio_analytics import compute_snapshot
from app.services.portfolio_risk_service import portfolio_risk
from app.services import commodities as _comm
from app.services import strategies as _strat
from app.services.recommendations import build_recommendations


async def gather(session: AsyncSession, user: User) -> dict:
    rows = await list_positions(session, user)
    pdicts = [{"ticker": p.ticker, "market": p.market, "quantity": float(p.quantity),
               "cost_basis": float(p.cost_basis), "current_price": float(p.current_price or 0),
               # CLASSIFIED, not read raw. This projection took
               # `meta["asset_class"]` straight off the row, so the sleeve
               # funder's "Equities" default reached the assistant and the
               # digest untouched -- and the breakdown below then applied a
               # SECOND `or "Equities"` on top of it. Two independent fallbacks
               # to the same wrong answer, in the one module whose stated job is
               # that "the LLM only ever sees these numbers, so it can't invent
               # them". A number it cannot invent can still be wrong.
               "asset_class": classify_holding(p),
               "volatility_pct": (p.meta or {}).get("volatility_pct")} for p in rows]
    snap = compute_snapshot(pdicts) if pdicts else {"nav": 0.0}
    ctx: dict = {
        "nav_ils": round(snap.get("nav", 0.0), 2),
        "holdings": [{"ticker": d["ticker"], "qty": d["quantity"], "price": d["current_price"],
                      "value_ils": round(holding_value_ils(d), 2),
                      "asset_class": d["asset_class"]} for d in pdicts],
    }
    # asset-class concentration (so the assistant can reason about diversification)
    from collections import defaultdict
    byc: dict[str, float] = defaultdict(float)
    for d in pdicts:
        byc[d["asset_class"]] += holding_value_ils(d)
    _tot = sum(byc.values()) or 1.0
    ctx["asset_class_breakdown"] = {k: round(v / _tot, 3) for k, v in byc.items()}
    # what the user could add / switch to (grounding for "should I..." questions)
    ctx["available_commodity_options"] = [
        {"ticker": c["ticker"], "name": c["name"], "category": c["category"]} for c in _comm.CATALOG]
    ctx["available_strategies"] = [
        {"id": s["id"], "name": s["name"], "goal": s["goal"]} for s in _strat.CATALOG]
    try:
        recs = await build_recommendations(session, user)
        ctx["recommendations"] = [{"title": r["title"], "action": r.get("action")}
                                  for r in recs.get("recommendations", [])[:5]]
        ctx["beta_validation"] = recs.get("risk_validation")
    except Exception:
        pass
    plan = await get_plan(session, user)
    if plan:
        # PR 74 -- the ONE goal (goal_service), not the stored column: the
        # target, the money put in that it is built from, and the deadline.
        from app.services.goal_service import compute_goal, goal_from
        try:
            _g = await compute_goal(session, user, plan, None)
        except Exception:  # noqa: BLE001 -- a ledger read must not cost the digest
            _g = goal_from(plan, [], None)
        ctx["goal"] = {"target_ils": _g.get("target"), "put_in_ils": _g.get("put_in"),
                       "deadline": _g.get("deadline"),
                       "target_date": getattr(plan, "target_date", None),
                       "objective": getattr(plan, "objective", None),
                       "risk_tolerance": getattr(plan, "risk_tolerance", None)}
    try:
        r = await portfolio_risk(session, user)
        if r.get("ok"):
            # `goal` is DELIBERATELY NOT COPIED. It carries a second probability
            # of reaching the target, computed by a different engine from the
            # one the Plan screen shows -- and on 2026-09-02 it was `null` while
            # the push claimed "89.2% probability of reaching your 102,973 goal
            # in 5 years", against a plan of 100,288.77 over 6 years at 22.7%.
            # Two sources for one question is how a reader gets a third answer.
            ctx["risk"] = {k: r.get(k) for k in ("annualized_volatility_pct", "annualized_return_pct",
                           "var_95_1d_pct", "cvar_95_1d_pct", "beta", "avg_correlation")}
            # THE CONFIDENCE LEVEL IS A NUMBER THE DIGEST WILL WRITE, so it has
            # to be a number the digest was given. "1-day VaR (95%)" is the
            # standard way to say this and the 95 in it is part of the
            # measurement's NAME, not a figure -- but `unsupported_numbers`
            # cannot tell a label from a quantity and should not try. Putting it
            # in the context is the honest way to let it be quoted.
            if r.get("var_95_1d_pct") is not None:
                ctx["risk"]["var_confidence_pct"] = 95
    except Exception:
        pass
    try:
        pf = await performance(session, user)
        if pf.get("ok"):
            ctx["performance"] = {k: pf.get(k) for k in ("total_return_pct", "cagr_pct",
                                  "max_drawdown_pct", "benchmark", "benchmark_return_pct", "excess_return_pct")}
    except Exception:
        pass
    # THE ONE PLACE THE GOAL PROBABILITY COMES FROM, and the same one the Plan
    # screen reads. Without it the model has a target and no probability, which
    # is the vacuum the invented 89.2% filled.
    try:
        from app.services.projection_service import project as goal_projection
        _pj = await goal_projection(session=session, user=user)
        if isinstance(_pj, dict) and not _pj.get("message"):
            ctx["projection"] = {k: _pj.get(k) for k in
                                 ("years", "target_amount", "probability_meets_target",
                                  "projected_median", "assumption_basis")}
            # PR 120: the goal's ONE chance, as the Plan tab and Today quote it
            # (the rules as they are). The chart's no-rules run is the fallback.
            _gh = _pj.get("goal_headline") or {}
            if _gh.get("p_goal_pct") is not None:
                ctx["projection"]["probability_meets_target"] = round(float(_gh["p_goal_pct"]) / 100.0, 3)
                ctx["projection"]["probability_basis"] = (
                    "goal model, protective rules as they are"
                    + (f"; last solved {_gh.get('age_hours')}h ago and out of date "
                       f"({_gh.get('stale_text')}), re-solving" if _gh.get("current") is False else ""))
            else:
                ctx["projection"]["probability_basis"] = "rough projection, protective rules not simulated"
    except Exception:  # noqa: BLE001
        pass
    return ctx


#: What each quotable figure is a MEASUREMENT OF, in the words the digest should
#: use. Keyed by the dotted path into the context. A figure with no entry here is
#: still allowed in the text -- the verifier only checks that a number is
#: PRESENT in the context -- but anything a reader could misread on its own
#: belongs here, because the failure this fixes was a basis confusion and not an
#: invention.
FIGURE_BASIS = {
    "nav_ils": ("what your holdings are worth right now, at today's prices and "
                "today's FX rate"),
    "performance.total_return_pct": (
        "a BACKFILL: the holdings you hold TODAY, priced back through real "
        "closes, as if you had held them for the whole window. It is NOT what "
        "your account did, and it is NOT a return on your NAV"),
    "performance.cagr_pct": "the same backfill, expressed per year",
    "performance.max_drawdown_pct": "the worst peak-to-trough fall inside that backfill",
    "performance.benchmark_return_pct": "the benchmark over the same backfill window",
    "performance.excess_return_pct": "the backfill minus the benchmark, over the whole window",
    "risk.annualized_volatility_pct": "how much the book moves, annualised from recent history",
    "risk.beta": "how the book moves relative to the benchmark",
    "risk.var_95_1d_pct": "a one-day loss the book exceeds about 1 day in 20",
    "goal.target_ils": "the target amount you set in Plan. Not a forecast",
    "projection.probability_meets_target": (
        "the ONLY probability of reaching your goal. A fraction between 0 and 1: "
        "the Plan tab's chance with the protective rules as they are (see "
        "projection.probability_basis)"),
    "projection.years": "the horizon that probability is measured over",
    # Nominal: `goal_projection` returns `sim.nominal.p50` (review of PR 76 --
    # this said "real terms" while the figure was nominal).
    "projection.projected_median": "the median simulated end value, in nominal shekels",
}


def for_prompt(ctx: dict) -> dict:
    """A COPY of the context with every known figure labelled `{value, means}`.

    Separate from `gather` on purpose. The labels exist to constrain a PROMPT;
    `gather`'s output is also returned to the browser by `/api/v1/ask`, and
    wrapping the values there changes a payload shape that has readers -- a
    `context.nav_ils` that used to be a number becomes an object, and every one
    of them breaks quietly. Labelling at the seam where the text is generated
    keeps the API exactly as it was.

    Written as one table rather than a phrase beside each number so a figure
    added later without a basis is visibly missing from a single list.
    """
    out = json.loads(json.dumps(ctx, default=str))
    for path, means in FIGURE_BASIS.items():
        parts = path.split(".")
        node = out
        for p in parts[:-1]:
            node = node.get(p) if isinstance(node, dict) else None
            if not isinstance(node, dict):
                break
        if not isinstance(node, dict):
            continue
        leaf = parts[-1]
        if leaf in node and node[leaf] is not None and not isinstance(node[leaf], dict):
            node[leaf] = {"value": node[leaf], "means": means}
    return out
