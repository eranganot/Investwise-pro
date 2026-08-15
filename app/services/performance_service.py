"""Phase E - portfolio performance vs benchmark, backfilled from real price history.

Values the user's *current* holdings back through their real historical prices to
produce an immediate performance curve (clearly: a backfill of today's holdings,
not a trade-by-trade record), and compares it to a benchmark over the same dates.

**Currency.** Holdings are FX-normalized into the base currency before they are
summed. Without it the series added shekels to dollars: a TASE holding at 1,000
ILS and a US holding at 200 USD summed to 1,200, so the USD side was underweighted
by the whole FX rate and *every* figure derived from the series -- value, total
return, drawdown, and the excess vs the benchmark -- was wrong on any
mixed-currency book. The information needed to convert (`market`, `meta`) was
being dropped by the projection in `performance()`, one function earlier, so the
worker could not have converted even if it had tried. See
`tests/test_performance_fx.py`.

The rate is today's spot, held constant across the window, because `guarded_fx`
exposes no historical series. That removes the currency-MIX error, which is the
bug. It does NOT attribute any part of the return to currency movement, which is
a separate and honest limitation -- reported as `fx_basis: "spot"` so the card
can say so rather than implying otherwise.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.engines.performance import summarize
from app.models.tables import User
from app.core.offload import offload
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
    # Project to plain tuples HERE, on the event loop's own thread, then hand
    # the blocking half to a worker. Reading these columns is safe off-loop as
    # well, but projecting first means _performance_from holds no ORM object
    # at all -- so it cannot grow a relationship access later and start raising
    # MissingGreenlet (see app/core/offload.py).
    #
    # `market` and `meta` travel with the ticker because a holding cannot be
    # valued without knowing which currency it trades in. Projecting only
    # (ticker, quantity) is what made the series mix currencies: the worker was
    # not ignoring FX, it had been handed no way to apply it. `meta` is copied
    # rather than passed by reference so no ORM-owned object crosses the thread.
    holdings = [(p.ticker, float(p.quantity), p.market, dict(p.meta or {})) for p in rows]
    return await offload(_performance_from, holdings, history_days)


def _performance_from(holdings: list[tuple[str, float, str, dict]],
                      history_days: int) -> dict:
    """The blocking half of performance(): one history fetch per holding plus
    the benchmark, all synchronous urllib. Pure -- no session, no ORM."""
    from app.services.fx import fx_rate, price_currency

    cfg = get_settings()
    base = (getattr(cfg, "base_currency", None) or "ILS").upper()

    qty, maps, rate = {}, {}, {}
    unconverted: list[dict] = []
    for ticker, quantity, market, meta in holdings:
        try:
            series = guarded_history(ticker, history_days)  # [(date, close)]
        except Exception:  # noqa: BLE001
            series = []
        if len(series) < 3:
            continue
        ccy = price_currency(market, meta)
        r = fx_rate(ccy, base)
        # fx_rate fails SAFE to 1.0 so valuation never crashes. On a FOREIGN
        # currency a 1.0 is a missing rate, not a parity -- valuing a shekel
        # holding as though it were a dollar is exactly the invented number
        # investing-discipline forbids. Record it and report it degraded;
        # never let it pass as a measurement.
        if ccy != base and r == 1.0:
            unconverted.append({"ticker": ticker, "currency": ccy})
        qty[ticker] = quantity
        maps[ticker] = {d: c for d, c in series}
        rate[ticker] = r
    if not maps:
        return {"ok": False, "reason": "no usable price history for holdings"}

    bench_map = None
    try:
        bench_map = {d: c for d, c in guarded_history(cfg.benchmark_ticker, history_days)}
    except Exception:  # noqa: BLE001
        bench_map = None

    common = set.intersection(*[set(m) for m in maps.values()])
    if bench_map:
        common &= set(bench_map)
    dates = sorted(common)
    if len(dates) < 3:
        return {"ok": False, "reason": "not enough overlapping price history"}

    values = [sum(qty[t] * maps[t][d] * rate[t] for t in maps) for d in dates]
    # The benchmark is deliberately NOT converted. Under a constant spot rate a
    # currency conversion is a constant scaling, and a constant scaling cancels
    # in every return ratio -- so converting it would change no reported figure.
    # This stops being true the moment a historical FX series lands: a benchmark
    # left in USD against an ILS-converted portfolio would turn the excess into
    # a currency bet. Convert both here when `fx_history` exists.
    bench_vals = [bench_map[d] for d in dates] if bench_map else None
    summary = summarize(values, bench_vals)

    ds_dates, (pidx, bidx) = _downsample(
        dates, summary["portfolio_index"], summary["benchmark_index"] or summary["portfolio_index"])
    out = {
        "ok": True, "benchmark": cfg.benchmark_ticker, "source": market_provider().name,
        "holdings_analyzed": list(maps), "observations": len(dates),
        # The window, and what KIND of measurement this is. A ten-year strategy
        # backtest and this 250-day backfill of today's holdings will disagree,
        # and both are correct -- but a screen can only say so if each figure
        # carries its own provenance. See backtest_service._payload for the twin.
        "window": {"start": dates[0], "end": dates[-1],
                   "sessions": len(dates), "kind": "holdings_backfill"},
        "base_currency": base, "fx_basis": "spot",
        "start_value": round(values[0], 2), "end_value": round(values[-1], 2),
        # Legacy aliases. Truthful now that the series is converted, but they
        # hard-code a currency the settings can change; migrate the card to
        # start_value/end_value + base_currency and drop these.
        "start_value_ils": round(values[0], 2), "end_value_ils": round(values[-1], 2),
        "total_return_pct": summary["total_return_pct"], "cagr_pct": summary["cagr_pct"],
        "max_drawdown_pct": summary["max_drawdown_pct"],
        "benchmark_return_pct": summary["benchmark_return_pct"],
        "benchmark_cagr_pct": summary.get("benchmark_cagr_pct"),
        "excess_return_pct": summary["excess_return_pct"],
        "excess_cagr_pct": summary.get("excess_cagr_pct"),
        "dates": ds_dates, "portfolio_index": pidx,
        "benchmark_index": bidx if bench_map else None,
    }
    if unconverted:
        out["degraded"] = ["fx"]
        out["unconverted_holdings"] = unconverted
    return out
