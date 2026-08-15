"""T0.3(b) - the performance backfill must not add shekels to dollars.

`performance()` projected positions to `(ticker, quantity)` before handing them
to the worker, dropping `market` and `meta` -- the only inputs `price_currency`
has. So `_performance_from` summed raw closes in whatever currency each holding
happened to trade in. On a mixed book that underweights the foreign side by the
whole FX rate and corrupts value, total return, drawdown and the excess vs the
benchmark alike.

These tests assert on the computed series, not on wording.
"""
from __future__ import annotations

import pytest

from app.engines.performance import summarize
from app.services import performance_service as ps

DATES = [f"2026-01-{d:02d}" for d in range(1, 21)]
USDILS = 3.70


def _ramp(start: float, end: float):
    step = (end - start) / (len(DATES) - 1)
    return [(d, round(start + step * i, 6)) for i, d in enumerate(DATES)]


HISTORY = {
    "TASE_CO": _ramp(1000.0, 1000.0),   # ILS, flat
    "MSFT": _ramp(200.0, 240.0),        # USD, +20%
    "SPY": _ramp(600.0, 630.0),         # USD, +5%
}


@pytest.fixture
def priced(monkeypatch):
    """Deterministic history + FX, so the assertions are about the arithmetic."""
    monkeypatch.setattr(ps, "guarded_history", lambda tk, days: HISTORY[tk])

    class _Rate:
        def __init__(self, rate):
            self.rate = rate

    import app.providers.registry as registry
    monkeypatch.setattr(registry, "guarded_fx",
                        lambda ccy, base: _Rate({"USD": USDILS, "ILS": 1.0}[ccy]),
                        raising=False)
    return HISTORY


def test_mixed_currency_book_is_fx_normalized(priced):
    """One TASE holding and one US holding must be summed in one currency."""
    holdings = [("TASE_CO", 1.0, "TASE", {}), ("MSFT", 10.0, "NASDAQ", {})]
    out = ps._performance_from(holdings, 252)

    rate = {"TASE_CO": 1.0, "MSFT": USDILS}
    expected = [sum(q * dict(HISTORY[t])[d] * rate[t] for t, q, _m, _x in holdings)
                for d in DATES]
    reference = summarize(expected, [c for _, c in HISTORY["SPY"]])

    assert out["base_currency"] == "ILS"
    assert out["start_value"] == pytest.approx(expected[0])
    assert out["end_value"] == pytest.approx(expected[-1])
    assert out["total_return_pct"] == reference["total_return_pct"]
    assert out["excess_return_pct"] == reference["excess_return_pct"]
    # The pre-fix arithmetic summed 1000 + 10*200 = 3000 rather than 8400.
    assert out["start_value"] > sum(q * dict(HISTORY[t])[DATES[0]]
                                    for t, q, _m, _x in holdings)


def test_single_currency_book_returns_are_unchanged_by_conversion(priced):
    """Constant scaling cancels in a ratio: converting an all-USD book may move
    its VALUES but must not move any return, drawdown or excess figure."""
    usd = [("MSFT", 10.0, "NASDAQ", {}), ("SPY", 1.0, "NYSE", {})]
    at_parity = [("MSFT", 10.0, "TASE", {}), ("SPY", 1.0, "TASE", {})]  # rate 1.0

    a = ps._performance_from(usd, 252)
    b = ps._performance_from(at_parity, 252)

    for key in ("total_return_pct", "cagr_pct", "max_drawdown_pct",
                "excess_return_pct", "excess_cagr_pct"):
        assert a[key] == b[key], key
    assert a["start_value"] == pytest.approx(b["start_value"] * USDILS)


def test_a_missing_rate_is_reported_not_assumed(priced, monkeypatch):
    """fx_rate fails safe to 1.0. On a foreign currency that is a missing rate,
    not a parity -- it must surface rather than value a dollar as a shekel."""
    import app.providers.registry as registry

    def _down(ccy, base):
        raise RuntimeError("fx provider unavailable")

    monkeypatch.setattr(registry, "guarded_fx", _down, raising=False)
    out = ps._performance_from([("MSFT", 10.0, "NASDAQ", {})], 252)

    assert out["degraded"] == ["fx"]
    assert out["unconverted_holdings"] == [{"ticker": "MSFT", "currency": "USD"}]


def test_excess_is_available_on_both_bases(priced):
    """The card renders an annualized CAGR beside a total-period excess. Both
    bases must exist so like can be compared with like."""
    out = ps._performance_from([("MSFT", 10.0, "NASDAQ", {})], 252)

    assert out["excess_return_pct"] is not None      # total period
    assert out["excess_cagr_pct"] is not None        # annualized
    assert out["benchmark_cagr_pct"] is not None
    assert out["excess_cagr_pct"] == pytest.approx(
        out["cagr_pct"] - out["benchmark_cagr_pct"], abs=0.02)
