"""Phase D - assemble the portfolio-risk report from live price history."""
from __future__ import annotations

import datetime as _dt

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.engines.backtest_engine import BacktestEngine
from app.engines.portfolio_risk import analyze, pct_returns
from app.models.tables import User
from app.providers.registry import guarded_history, market_provider
from app.services.intake_service import list_positions
from app.services.plan_service import get_plan


def _years_from_plan(plan) -> float | None:
    if plan is None:
        return None
    try:
        yr = int(str(getattr(plan, "target_date", "") or "")[:4])
        y = yr - _dt.date.today().year
        if y > 0:
            return float(y)
    except (ValueError, TypeError):
        pass
    h = getattr(plan, "horizon_years", None)
    return float(h) if h else None


async def portfolio_risk(session: AsyncSession, user: User, *, history_days: int = 252) -> dict:
    rows = await list_positions(session, user)
    if not rows:
        return {"ok": False, "reason": "no holdings"}

    tickers, weights, nav, maps = [], [], 0.0, {}
    for p in rows:
        value = float(p.quantity) * float(p.current_price or 0)
        try:
            series = guarded_history(p.ticker, history_days)  # [(date, close), ...]
        except Exception:
            series = []
        if len(series) < 3:
            continue
        tickers.append(p.ticker)
        weights.append(value)
        nav += value
        maps[p.ticker] = {d: c for d, c in series}
    if not tickers:
        return {"ok": False, "reason": "no usable price history for holdings"}

    cfg = get_settings()
    bench_map = None
    try:
        bench_map = {d: c for d, c in guarded_history(cfg.benchmark_ticker, history_days)}
    except Exception:
        bench_map = None

    # Align every series on the dates common to ALL holdings (and the benchmark),
    # so correlations/beta are computed on matching trading days - not by length.
    common = set.intersection(*[set(m) for m in maps.values()])
    if bench_map:
        common &= set(bench_map)
    dates = sorted(common)
    if len(dates) < 3:
        return {"ok": False, "reason": "not enough overlapping price history"}
    hist = {t: [maps[t][d] for d in dates] for t in tickers}
    bench_rets = pct_returns([bench_map[d] for d in dates]) if bench_map else None

    plan = await get_plan(session, user)
    target = float(plan.target_amount) if plan and getattr(plan, "target_amount", None) else None
    years = _years_from_plan(plan)

    report = analyze(tickers=tickers, weights=weights, history_by_ticker=hist,
                     benchmark_returns=bench_rets, nav=nav, target=target, years=years)
    report["nav"] = round(nav, 2)
    report["benchmark"] = cfg.benchmark_ticker
    report["source"] = market_provider().name
    report["holdings_analyzed"] = tickers

    # Phase 3.3 cross-check: validate structural vs realized (historical) beta.
    if report.get("ok") and report.get("beta") is not None:
        bt = BacktestEngine().run(
            [{"ticker": t, "asset_class": "Equities", "value_ils": w}
             for t, w in zip(tickers, weights)],
            realized_beta=report["beta"])
        report["beta_validation"] = {"structural_beta": bt.structural_beta,
                                     "realized_beta": report["beta"],
                                     "validated": bt.beta_validated, "note": bt.critique}
    return report
