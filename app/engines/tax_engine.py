"""4.1 TAX ENGINE - net-after-tax optimization (Phase 1).

Israeli model, all rates/thresholds config-driven (see .env / Settings):
  * CGT  = cgt_rate (default 25%) on the taxable gain (after loss offset).
  * Surtax (mas yesef) = surtax_rate (default 5%) applied at the MARGIN: only
    the portion of the gain that sits above surtax_threshold_ils, given the
    taxpayer's prior taxable income for the year.
  * Loss carry-forward offsets the gain before CGT; `tax_saved` is the benefit
    of that offset vs. taxing the full gain.

These figures are estimates for planning, not tax advice - confirm with an
accountant before acting.
"""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.schemas.state_machine import ActionType, OptimizedSignal, VettedSignal
from app.schemas.tax import TaxBreakdown


class TaxEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _tax_on(self, taxable_gain: float, prior_income: float) -> tuple[float, float]:
        """Return (cgt, surtax) for a taxable gain given prior annual income."""
        s = self.settings
        cgt = s.cgt_rate * taxable_gain
        # Marginal surtax: the slice of THIS gain that lands above the threshold.
        base_above = max(0.0, prior_income + taxable_gain - s.surtax_threshold_ils)
        prior_above = max(0.0, prior_income - s.surtax_threshold_ils)
        surtax = s.surtax_rate * (base_above - prior_above)
        return cgt, surtax

    def compute(
        self,
        gross_gain: float,
        prior_taxable_income: float = 0.0,
        loss_carry_forward: float = 0.0,
    ) -> TaxBreakdown:
        if gross_gain <= 0:
            # Realized loss: no CGT; it becomes/extends a carry-forward asset.
            return TaxBreakdown(
                gross_gain=gross_gain,
                losses_applied=0.0,
                taxable_gain=0.0,
                cgt=0.0,
                surtax=0.0,
                total_tax=0.0,
                net_gain=gross_gain,
                tax_saved=0.0,
                effective_rate=0.0,
                surtax_applies=False,
                notes="Realized loss - no CGT; adds to loss carry-forward.",
            )

        losses_applied = min(max(0.0, loss_carry_forward), gross_gain)
        taxable_gain = gross_gain - losses_applied
        cgt, surtax = self._tax_on(taxable_gain, prior_taxable_income)
        total_tax = cgt + surtax

        # Counterfactual: same gain taxed WITHOUT applying carry-forward losses.
        cgt0, surtax0 = self._tax_on(gross_gain, prior_taxable_income)
        tax_saved = (cgt0 + surtax0) - total_tax

        net_gain = gross_gain - total_tax
        return TaxBreakdown(
            gross_gain=gross_gain,
            losses_applied=losses_applied,
            taxable_gain=taxable_gain,
            cgt=cgt,
            surtax=surtax,
            total_tax=total_tax,
            net_gain=net_gain,
            tax_saved=tax_saved,
            effective_rate=total_tax / gross_gain,
            surtax_applies=surtax > 0,
        )

    def optimize(self, signal: VettedSignal) -> OptimizedSignal:
        """State-machine transition: attach net-after-tax economics."""
        detected = signal.source
        gross = detected.gross_gain_ils
        if gross is None:
            # SYSTEMS rule: no economics provided -> Awaiting Data (all None).
            return OptimizedSignal(source=signal)

        b = self.compute(
            gross_gain=gross,
            prior_taxable_income=detected.prior_taxable_income_ils,
            loss_carry_forward=detected.loss_carry_forward_ils,
        )

        # Selling realizes the tax now; buying/holding/rebalancing defers it.
        if detected.action_type == ActionType.SELL:
            actual_tax_cost = b.total_tax
            tax_deferred = 0.0
        else:
            actual_tax_cost = 0.0
            tax_deferred = b.total_tax

        return OptimizedSignal(
            source=signal,
            net_gain_delta=b.net_gain,
            actual_tax_cost=actual_tax_cost,
            tax_saved=b.tax_saved,
            tax_deferred=tax_deferred,
        )
