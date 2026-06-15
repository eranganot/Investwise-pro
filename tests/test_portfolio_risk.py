"""Phase D - portfolio-level risk (correlations, VaR/CVaR, beta, goal MC)."""
import numpy as np
from fastapi.testclient import TestClient

from app.engines.portfolio_risk import analyze, pct_returns
from app.main import app


def _series(seed, n=160, mu=0.0004, sig=0.011):
    rng = np.random.default_rng(seed)
    p, out = 100.0, []
    for r in rng.normal(mu, sig, n):
        p *= (1 + r); out.append(p)
    return out


def test_identical_assets_have_full_correlation_and_unit_beta():
    a = _series(1)
    r = analyze(tickers=["A", "B"], weights=[1, 1], history_by_ticker={"A": a, "B": a},
                benchmark_returns=pct_returns(a), nav=100_000)
    assert r["ok"] and r["avg_correlation"] > 0.99
    assert abs(r["beta"] - 1.0) < 0.05
    assert r["annualized_volatility_pct"] > 0
    assert r["var_95_1d_pct"] > 0 and r["cvar_95_1d_pct"] >= r["var_95_1d_pct"]


def test_independent_assets_have_low_correlation():
    same = analyze(tickers=["A", "B"], weights=[1, 1],
                   history_by_ticker={"A": _series(1), "B": _series(1)}, nav=1)["avg_correlation"]
    indep = analyze(tickers=["A", "B"], weights=[1, 1],
                    history_by_ticker={"A": _series(1), "B": _series(2)}, nav=1)["avg_correlation"]
    assert indep < same


def test_goal_probability_present_with_target():
    a = _series(3)
    r = analyze(tickers=["A"], weights=[1], history_by_ticker={"A": a},
                nav=100_000, target=120_000, years=5)
    assert r["goal"] and 0.0 <= r["goal"]["prob_reach"] <= 1.0


def test_insufficient_history_is_handled():
    r = analyze(tickers=["A"], weights=[1], history_by_ticker={"A": [100.0]}, nav=1000)
    assert r["ok"] is False


def test_portfolio_risk_endpoint(monkeypatch):
    port = {"entity_name": "Personal", "positions": [
        {"ticker": "AAA", "market": "NYSE", "asset_class": "Equities", "depth": 2,
         "spot_price": 1, "listing_price": 1, "quantity": 100, "cost_basis": 50},
        {"ticker": "BBB", "market": "NYSE", "asset_class": "Equities", "depth": 2,
         "spot_price": 1, "listing_price": 1, "quantity": 50, "cost_basis": 80}]}
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=port)
        c.post("/api/v1/portfolio/refresh-prices")   # builtin gives prices -> nonzero value
        r = c.post("/api/v1/portfolio/risk").json()
        assert r["ok"] is True
        assert r["annualized_volatility_pct"] > 0 and r["beta"] is not None
        assert "beta_validation" in r and "structural_beta" in r["beta_validation"]
        for tk in ("AAA", "BBB"):
            c.delete("/api/v1/portfolio/position", params={"ticker": tk, "market": "NYSE"})
