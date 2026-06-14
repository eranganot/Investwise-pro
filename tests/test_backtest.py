"""Phase 3.3 - historical backtesting + beta validation."""
from fastapi.testclient import TestClient

from app.engines.backtest_engine import BacktestEngine
from app.main import app


def test_pure_equity_book_tracks_market_drawdown():
    # beta ~ 1.0 -> portfolio drawdown ~ market drawdown for each event.
    rep = BacktestEngine().run([{"ticker": "SPY", "asset_class": "Equities", "value_ils": 100_000}])
    assert abs(rep.structural_beta - 1.0) < 1e-6
    for e in rep.events:
        assert abs(e.portfolio_drawdown_pct - e.market_drawdown_pct) < 0.6
    # 2008 should be the worst, deeply negative
    assert rep.worst_event == "GFC_2008"
    assert rep.worst_portfolio_drawdown_pct > 30


def test_bond_heavy_book_has_lower_beta_and_drawdown():
    eq = BacktestEngine().run([{"ticker": "SPY", "asset_class": "Equities", "value_ils": 100_000}])
    bonds = BacktestEngine().run([{"ticker": "AGG", "asset_class": "Fixed Income", "value_ils": 100_000}])
    assert bonds.structural_beta < eq.structural_beta
    assert bonds.worst_portfolio_drawdown_pct < eq.worst_portfolio_drawdown_pct


def test_beta_validation_flags_divergence():
    eng = BacktestEngine()
    h = [{"ticker": "SPY", "asset_class": "Equities", "value_ils": 100_000}]  # structural beta 1.0
    # implied beta = vol/market_vol = 16/16 = 1.0 -> validated
    ok = eng.run(h, portfolio_vol_pct=16.0)
    assert ok.beta_validated and ok.risk_implied_beta == 1.0
    # a wildly high vol -> implied beta ~3 -> diverges from 1.0 -> flagged
    bad = eng.run(h, portfolio_vol_pct=48.0)
    assert bad.beta_validated is False
    assert "DIVERGENCE" in bad.critique


def test_backtest_endpoint_and_recommendations_validation():
    PORT = {"entity_name": "Personal", "positions": [
        {"ticker": "TEVA", "market": "NYSE", "asset_class": "Equities", "depth": 3,
         "spot_price": 100, "listing_price": 108, "quantity": 300, "cost_basis": 75,
         "volatility_pct": 14}]}
    with TestClient(app) as c:
        c.post("/api/v1/intake/portfolio", json=PORT)
        bt = c.post("/api/v1/backtest").json()
        assert bt["events"] and "structural_beta" in bt
        recs = c.get("/api/v1/recommendations").json()
        assert "risk_validation" in recs and "beta_validated" in recs["risk_validation"]
        c.delete("/api/v1/portfolio/position", params={"ticker": "TEVA", "market": "NYSE"})
