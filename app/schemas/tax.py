"""Tax computation result (Section 4.1 output)."""
from __future__ import annotations

from pydantic import BaseModel


class TaxBreakdown(BaseModel):
    gross_gain: float            # realized/realizable gain (ILS); negative = loss
    losses_applied: float        # carry-forward losses used to offset this gain
    taxable_gain: float          # gain after loss offset
    cgt: float                   # 25% capital gains tax
    surtax: float                # 5% marginal surtax above threshold
    total_tax: float             # cgt + surtax
    net_gain: float              # gross_gain - total_tax
    tax_saved: float             # vs. computing the same gain without losses
    effective_rate: float        # total_tax / gross_gain (0 if gross<=0)
    surtax_applies: bool
    notes: str = ""
