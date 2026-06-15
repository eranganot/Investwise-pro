"""Phase E - portfolio performance vs benchmark, backfilled from real price history.

Values the user's *current* holdings back through their real historical prices to
produce an immediate performance curve (clearly: a backfill of today's holdings,
not a trade-by-trade record), and compares it to a benchmark over the same dates.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.engines.performance import summarize
from app.models.tables import User
from app.providers.registry import guarded_history, market_provider
from app.services.intake_service import list_positions

_MAX_POINTS = 160  # keep the chart payload light


def _downsample(dates, *series):
    n = len(dates)
    if n <= _MAX_POINTS:
        return dates, series
    step = max(1, n // _MAX_POINTS)
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return [dates[i] for i in idx], tuple([s[i] for i in idx] for s in series)


async def performance(session: AsyncSession, user: User, *, history_days: int = 252) -> dict:
    rows = await list_positions(session, user)
    if not rows:
        return {"ok": False, "reason": "no holdings"}

    qty, maps = {}, {}
    for p in rows:
        try:
            series = guarded_history(p.ticker, history_days)  # [(date, close)]
        except Exception:
            series = []
        if len(series) < 3:
            continue
        qty[p.ticker] = float(p.quantity)
        maps[p.ticker] = {d: c for d, c in series}
    if not maps:
        return {"ok": False, "reason": "no usable price history for holdings"}

    cfg = get_settings()
    bench_map = None
    try:
        bench_map = {d: c for d, c in guarded_history(cfg.benchmark_ticker, history_days)}
    except Exception:
        bench_map = None

    common = set.intersection(*[set(m) for m in maps.values()])
    if bench_map:
        common &= set(bench_map)
    dates = sorted(common)
    if len(dates) < 3:
        return {"ok": False, "reason": "not enough overlapping price history"}

    values = [sum(qty[t] * maps[t][d] for t in maps) for d in dates]
    bench_vals = [bench_map[d] for d in dates] if bench_map else None
    summary = summarize(values, bench_vals)

    ds_dates, (pidx, bidx) = _downsample(
        dates, summary["portfolio_index"], summary["benchmark_index"] or summary["portfolio_index"])
    return {
        "ok": True, "benchmark": cfg.benchmark_ticker, "source": market_provider().name,
        "holdings_analyzed": list(maps), "observations": len(dates),
        "start_value_ils": round(values[0], 2), "end_value_ils": round(values[-1], 2),
        "total_return_pct": summary["total_return_pct"], "cagr_pct": summary["cagr_pct"],
        "max_drawdown_pct": summary["max_drawdown_pct"],
        "benchmark_return_pct": summary["benchmark_return_pct"],
        "excess_return_pct": summary["excess_return_pct"],
        "dates": ds_dates, "portfolio_index": pidx,
        "benchmark_index": bidx if bench_map else None,
    }
