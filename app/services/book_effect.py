"""Does a strategy make the WHOLE book better? (PR 129, owner 2026-10-13)

The gate judges each candidate on its own against its base. The owner asked for
the test to carry one more measurement: what the candidate does to his actual
book -- core and sleeves at today's sizes -- when it is added at its suggested
size, with the core giving up the room. It is recorded and shown beside the five
gate tests; it does not change who passes (the owner chose option A).

THE RULE, fixed before anything was measured (sent to the owner 2026-10-13):
a strategy *improves the book* when, over the ten-year window, adding it

  * raises the book's return per unit of volatility by at least RATIO_GAIN, and
  * deepens the book's worst drop by no more than DD_TOLERANCE_PTS.

Measured with `blend.measure_blend`, the engine the target solver uses, on the
book `target_solver._book_for_solve` loads -- one loader, so this cannot drift
from what the solver believes the book is. Read-only: nothing is saved.

Not financial advice.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.offload import offload
from app.engines import blend
from app.engines import strategy_backtest as bt
from app.models.tables import User
from app.services import strategy_candidates, strategy_catalog

RATIO_GAIN = 0.02            # default; the setting is `book_effect_ratio_gain` (PR 133)
DD_TOLERANCE_PTS = 2.0       # default; the setting is `book_effect_drop_tolerance_pts` (PR 133)


def limits() -> tuple[float, float]:
    """The two limits in force for the user being served. Read in the request's
    context and passed to the worker: a thread does not inherit the overrides."""
    from app.services import thresholds
    return (float(thresholds.get("book_effect_ratio_gain", RATIO_GAIN)),
            float(thresholds.get("book_effect_drop_tolerance_pts", DD_TOLERANCE_PTS)))


def rule_text(ratio_gain: float = RATIO_GAIN, drop_tol: float = DD_TOLERANCE_PTS) -> str:
    return (f"improves the book when adding it raises return per unit of volatility by at least "
            f"{ratio_gain:g} and deepens the worst drop by no more than {drop_tol:g} points, "
            f"over the ten-year window")


RULE = rule_text()


def verdict(gain: float | None, deeper: float, ratio_gain: float, drop_tol: float) -> dict:
    """PR 133 (owner: "is that suggestion that no one of the sleeves are good to be
    taken?"): WHICH test failed and by how much, not just "does not improve"."""
    failed = []
    if gain is None or gain < ratio_gain:
        failed.append({"test": "ratio", "got": gain, "limit": ratio_gain,
                       "text": ("return per unit of risk could not be measured" if gain is None else
                                f"return per unit of risk moves {gain:+.3f}; your limit is +{ratio_gain:g}")})
    if deeper > drop_tol:
        failed.append({"test": "drop", "got": deeper, "limit": drop_tol,
                       "text": f"the worst drop gets {deeper:.1f} points deeper; your limit is {drop_tol:g}"})
    return {"improves": not failed, "failed": failed}


def _lookup(strategy_id: str) -> tuple[dict | None, dict | None]:
    """(engine spec, entry) from the catalogue, else from the failed candidates."""
    entry = strategy_catalog.get(strategy_id)
    if entry:
        return strategy_catalog.backtestable([strategy_id])[0], entry
    entry = strategy_candidates.get(strategy_id)
    if entry:
        return strategy_candidates.spec(strategy_id), entry
    return None, None


def _engine_spec(spec: dict) -> dict:
    """A proxy-measured rule enters the blend as its proxy fund -- the same
    thing its own headline was measured on."""
    proxy = (spec.get("proxy") or {}).get("ticker")
    if proxy:
        return {"id": spec.get("id"), "weights": {proxy: 1.0}, "overlay": {"kind": "buy_hold"}}
    return spec


def _summary(m: dict) -> dict:
    vol = m.get("volatility_pct") or 0.0
    return {"cagr_pct": m.get("cagr_pct"), "volatility_pct": m.get("volatility_pct"),
            "max_drawdown_pct": m.get("max_drawdown_pct"),
            "return_per_risk": round(m["cagr_pct"] / vol, 3) if vol else None,
            "start": m.get("start"), "end": m.get("end")}


def compare_blocking(book: dict, cand: dict, pct: float, fetch=None, *,
                     ratio_gain: float | None = None, drop_tol: float | None = None) -> dict:
    """Measure the book without and with ``cand`` at ``pct``% of NAV. Blocking."""
    from app.services.backtest_service import _fetch

    fetch = fetch or _fetch
    sid = cand["id"]
    floor = float(book.get("floor") or 0.0)
    others = [s for s in book["sleeves"] if s["id"] != sid]
    held = next((s for s in book["sleeves"] if s["id"] == sid), None)
    others_w = sum(float(s["current_pct"]) for s in others) / 100.0
    free = max(0.0, 1.0 - floor - others_w)          # core + this candidate's room
    add = min(float(pct) / 100.0, free)
    core = book.get("core") or {}

    def comps(with_cand: bool) -> list[dict]:
        out = [{"id": s["id"], "spec": s["spec"], "weight": float(s["current_pct"]) / 100.0}
               for s in others]
        core_w = free - (add if with_cand else 0.0)
        if core.get("weights") and core_w > 0.0005:
            out.append({"id": "core", "spec": core, "weight": core_w})
        if with_cand and add > 0:
            out.append({"id": sid, "spec": _engine_spec(cand), "weight": add})
        return out

    without, with_ = comps(False), comps(True)
    from app.core.config import get_settings
    bench_tk = book.get("bench_tk") or get_settings().benchmark_ticker
    need = {bench_tk}
    for c in without + with_:
        need |= set(bt.tickers_needed(c["spec"]))
    series, missing = fetch(sorted(need))
    if missing:
        return {"ok": False, "reason": bt.MISSING_TICKER,
                "detail": f"no price history for {', '.join(missing)}"}
    bench = series.get(bench_tk)

    def run(cs: list[dict]) -> dict:
        tks = set()
        for c in cs:
            tks |= set(bt.tickers_needed(c["spec"]))
        return blend.measure_blend({t: series[t] for t in tks if t in series}, cs,
                                   benchmark=bench, detail=False)

    a, b = run(without), run(with_)
    if not a.get("ok") or not b.get("ok"):
        bad = a if not a.get("ok") else b
        return {"ok": False, "reason": bad.get("reason"), "detail": bad.get("detail")}
    sa, sb = _summary(a), _summary(b)
    gain = (round(sb["return_per_risk"] - sa["return_per_risk"], 3)
            if sa["return_per_risk"] is not None and sb["return_per_risk"] is not None else None)
    deeper = round(float(sb["max_drawdown_pct"]) - float(sa["max_drawdown_pct"]), 2)
    rg, dt = (ratio_gain if ratio_gain is not None else RATIO_GAIN,
              drop_tol if drop_tol is not None else DD_TOLERANCE_PTS)
    v = verdict(gain, deeper, rg, dt)
    return {"ok": True, "strategy_id": sid, "sleeve_pct": round(add * 100, 2),
            "already_held": held is not None,
            "without": sa, "with": sb,
            "return_per_risk_gain": gain, "worst_drop_change_pts": deeper,
            "improves": v["improves"], "failed": v["failed"],
            "limits": {"ratio_gain": rg, "drop_tolerance_pts": dt},
            "rule": rule_text(rg, dt)}


async def for_strategy(session: AsyncSession, user: User, strategy_id: str,
                       sleeve_pct: float | None = None) -> dict:
    from app.services.target_solver import _book_for_solve

    spec, entry = _lookup(strategy_id)
    if not spec:
        return {"ok": False, "reason": "UNKNOWN_STRATEGY",
                "detail": f"{strategy_id!r} is neither in the catalogue nor a recorded candidate"}
    book, err = await _book_for_solve(session, user)
    if err:
        return {"ok": False, **err}
    pct = float(sleeve_pct if sleeve_pct is not None else entry.get("sleeve_pct") or 0.0)
    held = next((s for s in book["sleeves"] if s["id"] == strategy_id), None)
    if held and sleeve_pct is None:
        pct = float(held["current_pct"])            # its effect at the size you run
    rg, dt = limits()
    out = await offload(compare_blocking, book, spec, pct, ratio_gain=rg, drop_tol=dt)
    if isinstance(out, dict):
        out["name"] = entry.get("name")
        out["candidate_code"] = entry.get("code")
    return out


async def for_all_candidates(session: AsyncSession, user: User) -> dict:
    """The gate record: every pre-registered candidate against this book."""
    # Every candidate the plan pre-registered: the ones that failed or lost the
    # near-duplicate test (kept as specs) and the ones the catalogue took.
    ids = [c["id"] for c in strategy_candidates.CANDIDATES] + [
        "btm_qld_trend", "btm_tqqq_trend_cash", "btm_tqqq_trend_vol_brake"]
    rows = []
    for sid in ids:
        rows.append(await for_strategy(session, user, sid))
    return {"ok": True, "rule": rule_text(*limits()), "candidates": rows,
            "disclaimer": "Measured on your current book over one ten-year window. Not financial advice."}
