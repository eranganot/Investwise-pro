"""Synthetic price paths for the PR 77 holding-review tests (no network)."""
from datetime import date, timedelta
from types import SimpleNamespace


def up(n=300, start=100.0, step=0.004):
    xs = [start]
    for i in range(n - 1):
        xs.append(xs[-1] * (1 + step + (0.002 if i % 7 == 0 else -0.001)))
    return xs


def down_both(n=300):
    """Long uptrend, then a slide long enough to cross 50<200 and 20<50."""
    return up(200) + [up(200)[-1] * (1 - 0.006) ** (i + 1) for i in range(n - 200)]


def short_dip(n=300):
    """Long trend intact (50>200), last month falling (20<50)."""
    base = up(n - 25, step=0.003)
    return base + [base[-1] * (1 - 0.004) ** (i + 1) for i in range(25)]


def series(ticker, xs, d0=date(2016, 1, 4)):
    return {ticker: [((d0 + timedelta(days=i)).isoformat(), x) for i, x in enumerate(xs)]}


def row(ticker, qty, price, basis=100.0, market="TASE", created=None, pid="p1"):
    return SimpleNamespace(ticker=ticker, quantity=qty, current_price=price, cost_basis=basis,
                           market=market, meta={}, id=pid, created_at=created)


def measurement(edge_long=True, edge_short=True, age_days=0):
    from datetime import datetime, timezone
    from app.services.backtest_service import ENGINE_VERSION
    at = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()

    def j(edge):
        return {"measured": True, "edge": edge, "rule_cagr_pct": 12.0 if edge else 5.0,
                "hold_cagr_pct": 9.0, "rule_max_drawdown_pct": 20.0 if edge else 30.0,
                "hold_max_drawdown_pct": 25.0, "start": "2016-01-04", "end": "2026-09-25",
                "years": 10.7, "why": "beat holding with no deeper drawdown" if edge else
                "did worse than holding"}
    return {"engine_version": ENGINE_VERSION, "measured_at": at,
            "assumptions": ["review.signal.long@1", "review.signal.short@1"],
            "long": j(edge_long), "short": j(edge_short)}
