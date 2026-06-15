"""Decimal money tests (review C1)."""
import pytest

from app.core.money import D, money
from app.engines.tax_engine import TaxEngine

A = pytest.approx


def test_money_rounds_half_up_2dp():
    assert money(0.1 + 0.2) == 0.30          # float drift eliminated
    assert money(D("2.005")) == 2.01         # ROUND_HALF_UP
    assert money(16950) == 16950.0


def test_tax_uses_decimal_no_drift():
    # a value that drifts in float: 0.25 * 0.30 chained
    b = TaxEngine().compute(gross_gain=100_000.10, prior_taxable_income=0, loss_carry_forward=0)
    assert b.cgt == A(25_000.03)             # 25% of 100000.10, clean to cents
    assert b.total_tax == b.cgt              # no surtax under threshold
    assert b.net_gain == A(75_000.07)
