"""Honest-model tests (review C2): fat tails, assumptions, asset-class scenarios."""
import pytest

from app.engines.risk_engine import RiskEngine
from app.engines.scenario_engine import ScenarioEngine
from app.engines.simulation_engine import SimulationEngine
from app.engines.tax_engine import TaxEngine

A = pytest.approx


def test_risk_records_distribution_and_assumptions():
    n = RiskEngine(seed=5).monte_carlo(0.08, 0.18, distribution="normal")
    t = RiskEngine(seed=5).monte_carlo(0.08, 0.18, distribution="t")
    assert n.distribution == "normal" and t.distribution == "t"
    assert any("Monte" in a or "Brownian" in a for a in n.assumptions)
    assert any("Student-t" in a for a in t.assumptions)


def test_student_t_has_fatter_tail_at_low_vol():
    # at low vol, breaching the drawdown cap requires a tail event -> t > normal
    n = RiskEngine(seed=5).monte_carlo(0.08, 0.08, distribution="normal")
    t = RiskEngine(seed=5).monte_carlo(0.08, 0.08, distribution="t")
    assert t.probability_of_ruin > n.probability_of_ruin


def test_simulation_carries_assumptions():
    r = SimulationEngine(seed=11).run(initial_value=1_000_000, expected_return_pct=8,
                                      volatility_pct=15, horizon="year")
    assert r.assumptions and r.distribution == "normal"


def test_scenario_is_asset_class_aware_with_allocation():
    head = ScenarioEngine().run("INTEREST_RATE_INCREASE", 1_000_000)
    bonds = ScenarioEngine().run("INTEREST_RATE_INCREASE", 1_000_000,
                                 allocation={"Fixed Income": 0.8, "Equities": 0.2})
    assert head["asset_class_aware"] is False
    assert bonds["asset_class_aware"] is True
    # a bond-heavy book is hit harder by a rate increase than the headline blend
    assert bonds["expected_portfolio_value_delta_pct"] < head["expected_portfolio_value_delta_pct"]
    assert bonds["model_assumptions"]


def test_tax_breakdown_carries_assumptions():
    b = TaxEngine().compute(100_000)
    assert b.assumptions and any("tax professional" in a for a in b.assumptions)
