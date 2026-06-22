"""Tax Engine tests (Section 4.1) with worked numeric examples.

Defaults: CGT 25%, surtax 5%, threshold ILS 721,000.
"""
import pytest

from app.engines.tax_engine import TaxEngine
from app.schemas.state_machine import (
    ActionType,
    DetectedSignal,
    Market,
    VettedSignal,
)

A = pytest.approx


@pytest.fixture
def tax() -> TaxEngine:
    return TaxEngine()


def test_basic_cgt_no_surtax(tax):
    # 100k gain, no other income -> 25k CGT, no surtax.
    b = tax.compute(gross_gain=100_000)
    assert b.cgt == A(25_000)
    assert b.surtax == A(0)
    assert b.total_tax == A(25_000)
    assert b.net_gain == A(75_000)
    assert b.effective_rate == A(0.25)
    assert b.surtax_applies is False


def test_surtax_marginal_partially_above_threshold(tax):
    # Prior income 700k, gain 100k -> 79k of the gain sits above 721k.
    b = tax.compute(gross_gain=100_000, prior_taxable_income=700_000)
    assert b.cgt == A(25_000)
    assert b.surtax == A(0.05 * 79_000)   # 3,950
    assert b.total_tax == A(28_950)
    assert b.net_gain == A(71_050)
    assert b.surtax_applies is True


def test_surtax_fully_above_threshold(tax):
    # Prior income already above threshold -> entire gain incurs surtax.
    b = tax.compute(gross_gain=100_000, prior_taxable_income=800_000)
    assert b.surtax == A(5_000)           # 5% * 100k
    assert b.total_tax == A(30_000)


def test_loss_carry_forward_offset(tax):
    # 40k losses offset the 100k gain -> taxed on 60k.
    b = tax.compute(gross_gain=100_000, loss_carry_forward=40_000)
    assert b.losses_applied == A(40_000)
    assert b.taxable_gain == A(60_000)
    assert b.cgt == A(15_000)
    assert b.tax_saved == A(10_000)       # vs. 25k without losses
    assert b.net_gain == A(85_000)


def test_realized_loss_no_tax(tax):
    b = tax.compute(gross_gain=-50_000)
    assert b.total_tax == A(0)
    assert b.net_gain == A(-50_000)
    assert "carry-forward" in b.notes


def _signal(action: ActionType, gain: float | None) -> VettedSignal:
    det = DetectedSignal(
        ticker="X",
        market=Market.NYSE,
        action_type=action,
        trigger="t",
        depth=2,
        divergence_pct=5.0,
        gross_gain_ils=gain,
    )
    return VettedSignal(source=det)


def test_optimize_sell_realizes_tax_now(tax):
    opt = tax.optimize(_signal(ActionType.SELL, 100_000))
    assert opt.actual_tax_cost == A(25_000)
    assert opt.tax_deferred == A(0)
    assert opt.net_gain_delta == A(75_000)


def test_optimize_hold_defers_tax(tax):
    opt = tax.optimize(_signal(ActionType.REBALANCE, 100_000))
    assert opt.actual_tax_cost == A(0)
    assert opt.tax_deferred == A(25_000)


def test_optimize_awaiting_data_when_no_economics(tax):
    opt = tax.optimize(_signal(ActionType.BUY, None))
    assert opt.net_gain_delta is None
    assert opt.actual_tax_cost is None
    assert opt.tax_deferred is None


def test_rates_are_config_driven():
    from app.core.config import Settings
    eng = TaxEngine(Settings(cgt_rate=0.30, surtax_rate=0.0))
    b = eng.compute(gross_gain=100_000)
    assert b.cgt == A(30_000)
    assert b.surtax == A(0)
