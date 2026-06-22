"""Section X workflow tests (analytics + scenario engine, pure)."""
import pytest

from app.engines.scenario_engine import ScenarioEngine
from app.services.portfolio_analytics import (
    compute_snapshot, health_opportunities, health_scores, risk_alerts, tax_opportunities,
)

A = pytest.approx

POS = [
    {"ticker": "TEVA", "market": "NYSE", "quantity": 500, "cost_basis": 90, "current_price": 108, "volatility_pct": 12},
    {"ticker": "LOSS", "market": "TASE", "quantity": 100, "cost_basis": 120, "current_price": 90, "volatility_pct": 25},
]


def test_scenario_contract_values():
    r = ScenarioEngine().run("INFLATION_SHOCK", 1_000_000)
    assert r["scenario"] == "INFLATION_SHOCK"
    assert r["expected_portfolio_value_delta_pct"] == A(-4.2)
    assert r["drawdown_probability"] == A(0.35)
    assert r["projected_tax_impact_currency"] == A(0.25 * 0.042 * 1_000_000)  # 10500
    assert r["estimated_recovery_timeline_days"] == 180


def test_scenario_custom_and_invalid():
    r = ScenarioEngine().run("CUSTOM_SCENARIO", 1_000_000, custom_delta_pct=-10,
                             custom_drawdown=0.5, custom_recovery_days=90)
    assert r["expected_portfolio_value_delta_pct"] == A(-10)
    assert r["drawdown_probability"] == A(0.5)
    assert r["estimated_recovery_timeline_days"] == 90
    with pytest.raises(ValueError):
        ScenarioEngine().run("BOGUS", 1_000_000)


def test_snapshot_weights_and_unrealized():
    from app.services.fx import fx_rate
    usd = fx_rate("USD")  # TEVA is NYSE/USD; LOSS is TASE/ILS (rate 1.0) -> base-currency normalized
    snap = compute_snapshot(POS)
    assert snap["nav"] == A(500 * 108 * usd + 100 * 90)
    assert sum(snap["exposure_ticker"].values()) == A(1.0)
    assert snap["unrealized_gains"] == A((108 - 90) * 500 * usd)
    assert snap["unrealized_losses"] == A((120 - 90) * 100)


def test_health_check_caps_opportunities_at_five():
    snap = compute_snapshot(POS)
    sc = health_scores(snap)
    opps = health_opportunities(snap, sc)
    assert len(opps) <= 5
    assert any(o["dimension"] == "tax" for o in opps)        # losses present
    assert 0 <= sc["wealth_health_score"] <= 100


def test_tax_review_harvesting_savings():
    out = tax_opportunities(POS)
    harvest = [o for o in out["opportunities"] if o["trigger"] == "CAPITAL_LOSS_HARVESTING"][0]
    # offsetable = min(losses 3000, gains 9000) = 3000 -> 25% = 750
    assert harvest["estimated_annual_tax_savings_currency"] == A(750.0)
    assert out["total_estimated_annual_savings_currency"] > 0


def test_risk_alerts_single_position_breach():
    snap = compute_snapshot(POS)   # TEVA ~86% > 25% cap
    out = risk_alerts(snap)
    assert "single_position" in out["vectors_monitored"]
    assert any(a["vector"] == "single_position" and a["severity"] == "HIGH" for a in out["alerts"])
