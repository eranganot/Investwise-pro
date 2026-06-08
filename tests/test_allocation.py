"""Allocation Engine + Agent tests (Sections Y, AB)."""
import pytest

from app.agents.allocation_agent import AllocationAgent, VetoException
from app.engines.allocation_engine import AllocationEngine

A = pytest.approx


def test_spec_example_drift_and_sell_action():
    r = AllocationEngine().compute(
        target_allocation={"Equities": 0.60, "Fixed Income": 0.30, "Commodities": 0.10},
        current_allocation={"Equities": 0.64, "Fixed Income": 0.28, "Commodities": 0.08},
        nav=1_000_000)
    assert r.rebalance_required is True
    assert r.drift_percentage["Equities"] == A(0.04)
    acts = {a.asset_class: a for a in r.rebalance_actions}
    assert set(acts) == {"Equities"}                 # only equities breaches 0.03
    eq = acts["Equities"]
    assert eq.action_type == "SELL"
    assert eq.estimated_trade_value_currency == A(40_000)
    assert eq.tax_drag_currency == A(3_000)          # 0.25 * 0.30 * 40000
    assert eq.transaction_cost_currency == A(40)     # 10 bps
    assert eq.slippage_cost_currency == A(20)        # 5 bps
    assert eq.net_trade_value_currency == A(36_940)


def test_no_rebalance_within_threshold():
    r = AllocationEngine().compute(
        target_allocation={"Equities": 0.60, "Cash": 0.40},
        current_allocation={"Equities": 0.62, "Cash": 0.38}, nav=500_000)
    assert r.rebalance_required is False
    assert r.rebalance_actions == []


def test_underweight_triggers_buy_with_no_tax_drag():
    r = AllocationEngine().compute(
        target_allocation={"Equities": 0.60, "Fixed Income": 0.40},
        current_allocation={"Equities": 0.50, "Fixed Income": 0.50}, nav=1_000_000)
    eq = [a for a in r.rebalance_actions if a.asset_class == "Equities"][0]
    assert eq.action_type == "BUY"
    assert eq.tax_drag_currency == A(0)              # no CGT on a buy
    assert eq.net_trade_value_currency < eq.estimated_trade_value_currency  # costs still deducted


def test_agent_vetoes_buy_into_overweight_class():
    eng = AllocationEngine()
    report = eng.compute(
        target_allocation={"Equities": 0.60, "Fixed Income": 0.40},
        current_allocation={"Equities": 0.70, "Fixed Income": 0.30}, nav=1_000_000)
    agent = AllocationAgent(eng)
    with pytest.raises(VetoException):
        agent.review_buy("Equities", report)        # already overweight
    agent.review_buy("Fixed Income", report)         # underweight -> no veto
