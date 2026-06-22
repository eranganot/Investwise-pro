"""4.1 TAX ENGINE - net-after-tax optimization (Decimal money, review C1).

Israeli model, all rates/thresholds config-driven. CGT = cgt_rate on the taxable
gain (after loss offset); surtax = surtax_rate applied at the margin above the
threshold given prior income. Estimates for planning, not tax advice.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.config import Settings, get_settings
from app.core.money import D, money
from app.schemas.state_machine import ActionType, OptimizedSignal, VettedSignal
from app.schemas.tax import TaxBreakdown

_TAX_ASSUMPTIONS = [
    "flat CGT rate (no instrument-specific rates)",
    "marginal surtax approximation above the threshold",
    "no inflation-linked cost basis adjustment",
    "estimate only - confirm with a tax professional",
]

_Z = Decimal("0")


class TaxEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _tax_on(self, taxable_gain, prior_income) -> tuple[Decimal, Decimal]:
        s = self.settings
        tg, pi = D(taxable_gain), D(prior_income)
        thr, sr, cgt_rate = D(s.surtax_threshold_ils), D(s.surtax_rate), D(s.cgt_rate)
        cgt = cgt_rate * tg
        base_above = max(_Z, pi + tg - thr)
        prior_above = max(_Z, pi - thr)
        surtax = sr * (base_above - prior_above)
        return cgt, surtax

    def compute(self, gross_gain, prior_taxable_income=0.0, loss_carry_forward=0.0) -> TaxBreakdown:
        gross = D(gross_gain)
        if gross <= 0:
            return TaxBreakdown(
                gross_gain=money(gross), losses_applied=0.0, taxable_gain=0.0,
                cgt=0.0, surtax=0.0, total_tax=0.0, net_gain=money(gross),
                tax_saved=0.0, effective_rate=0.0, surtax_applies=False,
                notes="Realized loss - no CGT; adds to loss carry-forward.",
                assumptions=_TAX_ASSUMPTIONS)

        losses_applied = min(max(_Z, D(loss_carry_forward)), gross)
        taxable = gross - losses_applied
        cgt, surtax = self._tax_on(taxable, prior_taxable_income)
        total = cgt + surtax
        cgt0, surtax0 = self._tax_on(gross, prior_taxable_income)
        tax_saved = (cgt0 + surtax0) - total
        net = gross - total
        eff = float(total / gross) if gross > 0 else 0.0
        return TaxBreakdown(
            gross_gain=money(gross), losses_applied=money(losses_applied),
            taxable_gain=money(taxable), cgt=money(cgt), surtax=money(surtax),
            total_tax=money(total), net_gain=money(net), tax_saved=money(tax_saved),
            effective_rate=round(eff, 6), surtax_applies=surtax > 0, notes="",
            assumptions=_TAX_ASSUMPTIONS)

    def optimize(self, signal: VettedSignal) -> OptimizedSignal:
        detected = signal.source
        gross = detected.gross_gain_ils
        if gross is None:
            return OptimizedSignal(source=signal)
        b = self.compute(gross_gain=gross,
                         prior_taxable_income=detected.prior_taxable_income_ils,
                         loss_carry_forward=detected.loss_carry_forward_ils)
        if detected.action_type == ActionType.SELL:
            actual_tax_cost, tax_deferred = b.total_tax, 0.0
        else:
            actual_tax_cost, tax_deferred = 0.0, b.total_tax
        return OptimizedSignal(source=signal, net_gain_delta=b.net_gain,
                               actual_tax_cost=actual_tax_cost, tax_saved=b.tax_saved,
                               tax_deferred=tax_deferred)
