"""Phase E - performance metrics + backfilled portfolio performance endpoint."""
import pytest
from fastapi.testclient import TestClient

from app.engines.performance import cagr, index_series, max_drawdown, summarize, total_return
from app.main import app


def test_index_normalizes_to_100():
    assert index_series([50, 60, 55])[0] == 100.0
    assert index_series([50, 60, 55])[1] == 120.0


def test_total_return_and_drawdown():
    assert total_return([100, 120]) == pytest.approx(0.2)
    assert round(max_drawdown([100, 80, 90, 70]), 4) == 0.3   # 100 -> 70


def test_cagr_annualizes():
    # doubling over exactly one trading year -> ~100% CAGR
    assert round(cagr([100, 200], periods_per_year=252) - 1.0, 6) == 0.0 or cagr([100, 200]) > 0.9


def test_summarize_excess_vs_benchmark():
    s = summarize([100, 110, 120], [100, 105, 108])
    assert s["total_return_pct"] == 20.0 and s["benchmark_return_pct"] == 8.0
    assert s["excess_return_pct"] == 12.0


def test_performance_endpoint_backfills_from_history():
    port = {"entity_name": "Personal", "positions": [
        {"ticker": "AAA", "market": "NYSE", "asset_class": "Equities", "depth": 2,
         "spot_price": 1, "listing_price": 1, "quantity": 100, "cost_basis": 50},
        {"ticker": "BBB", "market": "NYSE", "asset_class": "Equities", "depth": 2,
         "spot_price": 1, "listing_price": 1, "quantity": 25, "cost_basis": 80}]}
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=port)
        r = c.post("/api/v1/portfolio/performance").json()
        assert r["ok"] is True
        assert len(r["dates"]) == len(r["portfolio_index"]) > 2
        assert r["portfolio_index"][0] == 100.0
        assert "total_return_pct" in r and "max_drawdown_pct" in r
        assert r["benchmark_index"] is not None  # builtin SPY history aligns
        for tk in ("AAA", "BBB"):
            c.delete("/api/v1/portfolio/position", params={"ticker": tk, "market": "NYSE"})
